from asyncore import loop
from base64 import b64encode
from contextlib import contextmanager
from json import loads, dumps
from logging import getLogger
from multiprocessing import Process, Pipe, current_process
from os import kill
from signal import SIGSTOP, SIGTERM
from socket import socket, AF_INET, SOCK_STREAM
from time import sleep
from unittest import TestCase, main as _main

from run import ConnectionHandler, main as server_main

from tests import log_to_stdout


LOGGER = getLogger(__name__)


SERVER_ADDRESS = ('127.0.0.1', 1111)


REQUEST_BUFFER_SIZE = 512


CODE = """
import functools

print("I pity the fool!")
b = 4*4
cube = lambda x: x*x*x
cube(3)

class Yo(Mama):
    def __init__(self, x):
        self.x = x
        super(Mama, self).__init__()


class Foo(Bar):
    def __init__(self):
        self.a = Yo(1)

"""

# fat code forces the asynchronous handler to go through multiple loops
# before it can get the entire request
FAT_CODE = (20*REQUEST_BUFFER_SIZE//len(CODE))*CODE


def request(code):
    return dumps({
        'code': b64encode(code),
        'context': {},
    })

REQUEST = request(CODE)


FAT_REQUEST = request(FAT_CODE)


def main():
    current_process().name = 'Server'
    server = ConnectionHandler(*SERVER_ADDRESS, request_buffer_size=REQUEST_BUFFER_SIZE)
    loop()


def client(pipe, connections):
    sockets = [ socket(AF_INET, SOCK_STREAM) for i in range(connections) ]
    LOGGER.debug('Creating %d connections to server', connections)
    for s in sockets:
        s.connect(SERVER_ADDRESS)
    LOGGER.debug('Done creating connections')
    while True:
        request = pipe.recv()
        if request:
            LOGGER.debug('Processing request')
            for s in sockets:
                s.sendall(request)
            responses = []
            for s in sockets:
                responses.append(s.recv(1024))
            pipe.send(responses)
            LOGGER.debug('Done processing request')
        else:
            # empty string is shutdown signal
            break
    for s in sockets:
        s.close()


def launch_client(_id, connections):
    pipe, client_pipe = Pipe()
    process = Process(target=client, args=(client_pipe, connections), name='Client '+str(_id))
    process.start()
    return process, pipe


@contextmanager
def parser():
    server = Process(
        target=server_main,
        args=SERVER_ADDRESS,
    )
    server.start()
    sleep(1)
    yield server
    if server.is_alive():
        server.terminate()


class AsyncTest(TestCase):

    def setUp(self):
        current_process().name = 'unittest'
        log_to_stdout()
        self.process = Process(target=main)
        self.process.start()

    def tearDown(self):
        if self.process.is_alive():
            self.process.terminate()

    def evaluate_client_response(self, response):
        self.assertIsNotNone(response)
        try:
            obj = loads(response)
        except:
            self.fail('cannot load client response as JSON')
        self.assertIsInstance(obj, dict)
        self.assertIn('use_count', obj)

    def single_client(self, _request):
        process, pipe = launch_client(0, 10)
        pipe.send(_request)
        responses = pipe.recv()
        pipe.send('') # shutdown
        process.join()
        for response in responses:
            self.evaluate_client_response(response)

    def test_client(self):
        LOGGER.debug('request size: %d', len(REQUEST))
        self.single_client(REQUEST)

    def test_client_fat_request(self):
        LOGGER.debug('request size: %d', len(FAT_REQUEST))
        self.single_client(FAT_REQUEST)

    def test_many_clients(self):
        num_clients = 2
        num_connections = 10
        clients = [ launch_client(i, num_connections) for i in range(num_clients) ]
        for _, pipe in clients:
            pipe.send(REQUEST)
        for index, (_, pipe) in enumerate(clients):
            responses = pipe.recv()
            self.assertEqual(len(responses), num_connections)
            for response in responses:
                self.evaluate_client_response(response)
        for process, pipe in clients:
            pipe.send('') # shutdown signal
            process.join()

    def test_stop(self):
        with parser() as server:
            kill(server.pid, SIGTERM)
            server.join()
            self.assertEqual(server.exitcode, SIGTERM)

    def test_many_clients_forked_server(self):
        num_clients = 10
        num_connections = 10
        with parser() as server:
            clients = [ launch_client(i, num_connections) for i in range(num_clients) ]
            for _, pipe in clients:
                pipe.send(REQUEST)
            for index, (_, pipe) in enumerate(clients):
                responses = pipe.recv()
                self.assertEqual(len(responses), num_connections)
                for response in responses:
                    self.evaluate_client_response(response)
            for process, pipe in clients:
                pipe.send('') # shutdown signal
                process.join()
    

if __name__ == '__main__':
    _main()


