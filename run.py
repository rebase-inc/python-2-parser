import os
import ast
import json
import base64
import logging
import rsyslog

from collections import Counter
from multiprocessing import current_process

import SocketServer

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


class CallbackRequestHandler(SocketServer.StreamRequestHandler):
    def handle(self):
        stream = self.request.makefile()
        data = ''
        while True:
            rawdata += stream.read()
            if not rawdata:
                return
            data += data.decode('utf-8').strip()
            try:
                data_as_object = json.loads(data)
                break
            except ValueError:
                continue
        code = base64.b64decode(data_as_object['code'])
        try:
            uses = ReferenceCollector().visit(ast.parse(code))
            stream.write(json.dumps(uses).encode('utf-8'))
        except SyntaxError as exc:
            LOGGER.info('Skipping due to syntax error: {}'.format(str(exc)))
            stream.write(json.dumps(ReferenceCollector().noop()).encode('utf-8'))
        except Exception as exc:
            LOGGER.info('Unhandled exception in python parser {}'.format(exc))
            stream.write(json.dumps(ReferenceCollector().noop()).encode('utf-8'))


if __name__ == '__main__':
    server = SocketServer.TCPServer(('0.0.0.0', 25253), CallbackRequestHandler)
    server.serve_forever()
