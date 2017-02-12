from logging import getLogger, Formatter, StreamHandler

from sys import stdout

def log_to_stdout():
    root_logger = getLogger()
    root_logger.setLevel('DEBUG')
    streamingHandler = StreamHandler(stdout)
    streamingHandler.setFormatter(Formatter('%(asctime)s %(levelname)s %(processName)s %(message)s'))
    root_logger.addHandler(streamingHandler)


