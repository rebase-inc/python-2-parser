import os
import ast
import json
import errno
import base64
import socket
import logging
import rsyslog
import asyncore

from collections import Counter
from multiprocessing import current_process

current_process().name = os.environ['HOSTNAME']
rsyslog.setup(log_level = os.environ['LOG_LEVEL'])
LOGGER = logging.getLogger()


class ReferenceCollector(ast.NodeVisitor):

    def __init__(self):
        super(ReferenceCollector, self).__init__()
        self.bindings = dict()
        self.use_count = Counter()

    def visit(self, node):
        super(ReferenceCollector, self).visit(node)
        self.use_count.update(['__grammar__'+node.__class__.__name__])
        return self.use_count

    def noop(self):
        return self.use_count

    def visit_Import(self, node):
        for alias in node.names:
            if alias.asname:
                self.bindings[alias.asname] = alias.name
            else:
                self.bindings[alias.name] = alias.name
            self.use_count.update([alias.name])

    def visit_ImportFrom(self, node):
        if node.level == 0:
            for alias in node.names:
                if alias.asname:
                    self.bindings[alias.asname] = node.module
                else:
                    self.bindings[alias.name] = node.module
                self.use_count.update([node.module])
        else:
            # relative import means private module
            pass

    def visit_Name(self, node):
        if node.id in self.bindings:
            self.use_count.update([self.bindings[node.id]])


def handle_data(sock):
    data_as_str = ''
    while True:
        rawdata = sock.recv(4096)
        if not rawdata:
            return
        data_as_str += rawdata.decode('utf-8').strip()
        try:
            json_object = json.loads(data_as_str)
            data_as_str = ''
        except ValueError:
            continue
        try:
            code = base64.b64decode(json_object['code'].encode('utf-8'))
            context = json_object['context']
            if 'path' not in context:
                LOGGER.warning('No filename provided to parser!')
                filename = '<unknown>'
            else:
                filename = context['path']
            data = {
                'use_count': ReferenceCollector().visit(
                    ast.parse(code, filename = filename)
                )
            }
        except KeyError as exc:
            data = { 'error': errno.EINVAL, 'message': str(exc) }
        except ValueError as exc:
            if exc.args[0].find('source code string cannot contain null bytes') >= 0:
                LOGGER.error('Skipping parsing because code contains null bytes!')
                data = { 'error': errno.EPERM, 'message': str(exc) }
            else:
                LOGGER.exception('Unhandled exception!')
                data = { 'error': errno.EIO, 'message': str(exc) }
        except SyntaxError as exc:
            LOGGER.debug('%s => %s', exc, exc.text)
            data = { 'error': errno.EIO, 'message': str(exc) }
        except Exception as exc:
            LOGGER.exception('Unhandled exception')
            data = { 'error': errno.EIO, 'message': str(exc) }
        sock.sendall(json.dumps(data))


# TODO: Move this into asynctcp library so we have a python2/3 capable async callback server
class CallbackRequestHandler(asyncore.dispatcher):

    def __init__(self, host, port):
        asyncore.dispatcher.__init__(self)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        self.bind((host, port))
        self.listen(5)

    def handle_accept(self):
        pair = self.accept()
        if pair is not None:
            sock, _ = pair
            handle_data(sock)

if __name__ == '__main__':
    server = CallbackRequestHandler('0.0.0.0', 25253)
    asyncore.loop()
