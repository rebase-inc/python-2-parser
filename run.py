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
        self.use_count = Counter()

    def __add_to_counter(self, name, allow_unrecognized = True):
        if allow_unrecognized or name in self.use_count:
            self.use_count.update([ name ])

    def visit(self, node):
        super(ReferenceCollector, self).visit(node)
        return self.use_count

    def noop(self):
        return self.use_count

    def visit_Import(self, node):
        for name in node.names:
            self.__add_to_counter(name.asname or name.name)

    def visit_ImportFrom(self, node):
        self.__add_to_counter(node.module)

    def visit_Name(self, node):
        self.__add_to_counter(node.id, allow_unrecognized = False)

def handle_data(sock):
    data_as_str = ''
    while True:
        rawdata = sock.recv(4096)
        if not rawdata:
            return
        data_as_str += rawdata.decode('utf-8').strip()
        try:
            data_as_object = json.loads(data_as_str)
            data_as_str = ''
        except ValueError:
            continue
        try:
            code = base64.b64decode(data_as_object['code'])
            uses = ReferenceCollector().visit(ast.parse(code))
            data = { 'use_count': ReferenceCollector().visit(ast.parse(code)) }
        except KeyError as exc:
            # handle malformed request data
            data = { 'error': errno.EINVAL }
        except SyntaxError as exc:
            # handle actual invalid code
            data = { 'error': errno.EIO }
        except ValueError as exc:
            if exc.args[0].find('source code string cannot contain null bytes') >= 0:
                LOGGER.error('Skipping parsing because code contains null bytes!')
                data = { 'error': errno.EPERM }
            else:
                LOGGER.exception('Unhandled exception!')
                data = { 'error': errno.EIO }
        except Exception as exc:
            LOGGER.exception('Unhandled exception')
            data = { 'error': errno.EIO }
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
