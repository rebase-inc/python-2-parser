import ast
import errno
from json import loads, dumps
import base64
from os import environ as env, kill
from signal import signal, SIGSTOP, SIGTERM, SIGCHLD, SIG_IGN
from socket import (
    error as socket_error,
    AF_INET,
    SOCK_STREAM,
    SO_REUSEPORT,
    SOL_SOCKET,
)
from sys import exit
from logging import getLogger
from asyncore import dispatcher, loop

from collections import Counter
from multiprocessing import current_process, cpu_count, Process

import rsyslog

from stdlib_list import stdlib_list

LOGGER = getLogger()

STANDARD_LIBRARY = stdlib_list('2.7')


class ReferenceCollector(ast.NodeVisitor):
    # see https://greentreesnakes.readthedocs.io/en/latest/nodes.html for good reference

    def __init__(self, private_namespace):
        super(ReferenceCollector, self).__init__()
        self.bindings = dict()
        self.use_count = Counter()
        self.bindings.update({ name: '__private__.' + name for name in private_namespace })
        self.bindings.update({ name: '__stdlib__.' + name for name in STANDARD_LIBRARY })

    def add_grammar(self, node):
        self.use_count.update(['__stdlib__.__grammar__.' + node.__class__.__name__])

    def visit(self, node):
        super(ReferenceCollector, self).visit(node)

    def generic_visit(self, node):
        self.add_grammar(node)
        super(ReferenceCollector, self).generic_visit(node)

    def add_binding(self, bound_name, *real_attributes):
        if bound_name in self.bindings:
            return
        elif real_attributes[0] in STANDARD_LIBRARY:
            self.bindings[bound_name] = '.'.join(['__stdlib__'] + list(real_attributes))
        elif real_attributes[0] in self.bindings:
            self.bindings[bound_name] = '.'.join([self.bindings[real_attributes[0]]] + list(real_attributes[1:]))
        else:
            self.bindings[bound_name] = '.'.join(real_attributes)

    def add_use(self, *attributes):
        if attributes[0] not in self.bindings:
            # we can't know all bindings, because of imports like "from foo import *"
            # In such a case, we can't really do anything with the reference
            return
        real_name = self.bindings[attributes[0]]
        full_name = '.'.join([real_name] + list(attributes[1:]))
        self.use_count.update([ full_name ])

    def visit_Import(self, node):
        self.add_grammar(node)
        for alias in node.names:
            self.add_binding(alias.asname or alias.name, *alias.name.split('.'))

    def visit_ImportFrom(self, node):
        self.add_grammar(node)
        if node.level == 0:
            for alias in node.names:
                real_name = node.module.split('.') + [alias.name]
                self.add_binding(alias.asname or alias.name, *real_name)
        else:
            # Relative import
            # TODO: Actually add this under __private__ namespace
            pass

    def visit_Attribute(self, attribute):
        name = self.get_name(attribute)
        self.add_use(*name)
        self.add_grammar(attribute)

    def get_name(self, node):
        if isinstance(node, ast.Name):
            return [ node.id ]
        elif isinstance(node, ast.Attribute):
            return self.get_attribute_name(node)
        elif isinstance(node, ast.Call):
            return self.get_call_name(node)
        elif isinstance(node, ast.Subscript):
            return self.get_name(node.value)
        else:
            return []

    def get_attribute_name(self, attribute):
        self.add_grammar(attribute)
        attributes = []
        expression = attribute
        while isinstance(expression, ast.Attribute):
            attributes.insert(0, expression.attr)
            expression = expression.value
        attributes = self.get_name(expression) + attributes
        return attributes

    def get_call_name(self, call):
        self.add_grammar(call)
        return self.get_name(call.func)

    def visit_Name(self, name):
        self.add_grammar(name)
        if name.id in self.bindings:
            self.add_use(name.id)



TRY_AGAIN_ERRORS = (errno.EAGAIN, errno.EWOULDBLOCK)


class RequestHandler(dispatcher):

    def __init__(self, sock, address, buffer_size=4096):
        dispatcher.__init__(self, sock=sock)
        self.address = address
        self.buffer_size = buffer_size
        self.data_as_str = ''
        self.encoded_response = ''

    def handle_read(self):
        try:
            data = self.recv(self.buffer_size)
        except socket_error as e:
            if e.errno and e.errno in TRY_AGAIN_ERRORS:
                return
            else:
                self.handle_error()
                return
        self.data_as_str += data.decode('utf-8').strip()
        try:
            request = loads(self.data_as_str)
            self.data_as_str = ''
        except ValueError:
            return
        try:
            code = base64.b64decode(request['code'].encode('utf-8'))
            context = request['context']
            reference_collector = ReferenceCollector(context['private_modules'] if 'private_modules' in context else [])
            reference_collector.visit(ast.parse(code, filename = context['filename'] if 'filename' in context else '<unknown>'))
            response = { 'use_count': reference_collector.use_count }
        except KeyError as exc:
            response = { 'error': errno.EINVAL, 'message': str(exc) }
        except ValueError as exc:
            if exc.args[0].find('source code string cannot contain null bytes') >= 0:
                LOGGER.error('Skipping parsing because code contains null bytes!')
                response = { 'error': errno.EPERM, 'message': str(exc) }
            else:
                LOGGER.exception('Unhandled exception!')
                response = { 'error': errno.EIO, 'message': str(exc) }
        except SyntaxError as exc:
            LOGGER.debug('%s => %s', exc, exc.text)
            response = { 'error': errno.EIO, 'message': str(exc) }
        except Exception as exc:
            LOGGER.exception('Unhandled exception')
            response = { 'error': errno.EIO, 'message': str(exc) }
        self.encoded_response = dumps(response)

    def handle_write(self):
        if self.encoded_response:
            sent = self.send(self.encoded_response)
            self.encoded_response = self.encoded_response[sent:]

    def handle_error(self):
        LOGGER.exception('Error in connection with {}'.format(self.address))
        self.encoded_response = ''


class ConnectionHandler(dispatcher):

    def __init__(self, host, port, request_buffer_size=4096):
        dispatcher.__init__(self)
        self.create_socket(AF_INET, SOCK_STREAM)
        self.set_reuse_addr()
        self.set_reuse_port()
        # Re-using port & address is the core of the idea:
        # since v3.9, Linux does connection balancing between multiple sockets listening to the same address,
        # as long as they're all under the same uid (to prevent stealing).
        # Connection balancing is not as good as request balancing:
        # in case one connection's request hogs a cpu, all connections associated with that cpu will be blocked and make no progress.
        # Also, long-live processes hold resources whether we use the service or not.
        # Up side is the implementation is trivial: just fork the process before the listening socket is setup.
        self.bind((host, port))
        self.listen(5)
        self.request_buffer_size = request_buffer_size
        LOGGER.debug('Listening at %s:%s',  host, port)

    def handle_accept(self):
        pair = self.accept()
        if pair:
            RequestHandler(*pair, buffer_size=self.request_buffer_size)

    def handle_close(self):
        LOGGER.debug('Closing listening socket')

    def handle_error(self):
        LOGGER.exception('Unhandled exception in ConnectionHandler')

    def set_reuse_port(self):
        try:
            self.socket.setsockopt(
                SOL_SOCKET,
                SO_REUSEPORT,
                self.socket.getsockopt(
                    SOL_SOCKET,
                    SO_REUSEPORT
                ) | 1
            )
        except socket_error:
            LOGGER.exception('Trying to set the SO_REUSEPORT option on the listening socket')


def async_loop(address, port, is_child=True, children=None):
    rsyslog.setup(log_level = env['LOG_LEVEL'] if 'LOG_LEVEL' in env else 'DEBUG')
    LOGGER.info('Booted')
    server = ConnectionHandler(address, port)
    if is_child:
        def shutdown_child(sig, frame):
            server.close()
            LOGGER.info('QUIT')
        signal(SIGTERM, shutdown_child)
    else:
        def shutdown_master(sig, frame):
            server.close()
            for child in children:
                kill(child.pid, SIGTERM)
            LOGGER.info('QUIT')
            exit(SIGTERM)
        signal(SIGTERM, shutdown_master)
        signal(SIGCHLD, SIG_IGN) # we don't join with children and don't want zombie
    loop()


def main(address, port):
    current_process().name = 'python_2_parser.master'
    children = [
        Process(
            target=async_loop,
            args=(address, port),
            name='python_2_parser.child.'+str(i)
        ) for i in range(cpu_count() - 1)
    ]
    map(Process.start, children)
    async_loop(address, port, is_child=False, children=children)


if __name__ == '__main__':
    main('0.0.0.0', 25253)


