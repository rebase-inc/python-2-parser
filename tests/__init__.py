from logging import getLogger, Formatter, StreamHandler

from sys import stdout

def log_to_stdout():
    root_logger = getLogger()
    root_logger.setLevel('DEBUG')
    streamingHandler = StreamHandler(stdout)
    streamingHandler.setFormatter(Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    root_logger.addHandler(streamingHandler)


log_to_stdout()


