from __future__ import print_function

import os
import sys
import code
import json
import logging
import argparse

from deoplete.server import stream, polling
from deoplete.server.server import BaseServer


class PickleLogHandler(logging.Handler):
    """Simple log handler that sends messages to the stderr stream.

    StreamHandler isn't used since it writes newlines.
    """
    def __init__(self, stream):
        super(PickleLogHandler, self).__init__()
        self.stream = stream

    def emit(self, record):
        self.stream.write(('log', -1, (record, self.format(record))))


class ServerConsole(code.InteractiveConsole):
    def __init__(self, cls, options):
        import readline
        import rlcompleter

        self.locals = {}
        readline.set_completer(rlcompleter.Completer(self.locals).complete)
        readline.parse_and_bind('tab: complete')

        code.InteractiveConsole.__init__(self, self.locals)

        self.log_stream, log_w = stream.test_pipes()

        self.log_poller = polling.ReadPoller()
        self.log_poller.register(self.log_stream)

        log = logging.getLogger()
        handler = PickleLogHandler(log_w)
        log.addHandler(handler)

        # self.log_handler = None
        # self.server_cls = cls
        # self.start_server_thread()

        from deoplete.server import process

        self.pm = process.ProcessManager(1, 1, 'testing.test', **options)
        self.pm.spawn()

        self.locals['command'] = self.run_command
        # self.locals['restart'] = self.restart_server

    def read_log(self):
        reading = True
        while reading:
            reading = False
            for fd in self.log_poller.poll(0.5):
                reading = True
                cmd, _, (record, msg) = stream.read_command(fd)
                print('%s: %s' % (logging.getLevelName(record.levelno), msg))

    def interact(self):
        self.read_log()
        code.InteractiveConsole.interact(self, '')
        self.read_log()

    def run_command(self, command, *args):
        print(self.pm.communicate(command, args))
        self.read_log()


def _run_server(cls, log, stdin, stdout, options):
    server_cls_str = '%s.%s' % (cls.__module__, cls.__name__)
    if not issubclass(cls, BaseServer):
        log.error('%s is not a subclass of BaseServer.', server_cls_str)
        return

    log.info('Starting server "%s", Script: %s',
             cls.__name__, sys.modules[cls.__module__].__file__)

    try:
        server = cls(log)
        server.configure(**options)
        server._run(stdin, stdout)
    except Exception:
        log.error('Server stopped unexpectedly.', exc_info=True)

    log.info('Server stopped: %s', server_cls_str)


def run_server(cls):
    parser = argparse.ArgumentParser()
    parser.add_argument('--log-level', type=int, required=False,
                        default=logging.DEBUG)

    args = parser.parse_args()

    try:
        options = json.loads(os.getenv('DEOPLETE_SERVER'))
        repl = False
    except (ValueError, TypeError):
        options = {}
        repl = True

    log = logging.getLogger()
    log.propagate = False
    log.setLevel(args.log_level)

    if not repl:
        stdin = stream.Reader(sys.stdin)
        stdout = stream.Writer(sys.stdout)
        stderr = stream.Writer(sys.stderr)
        handler = PickleLogHandler(stderr)
        log.addHandler(handler)
        _run_server(cls, log, stdin, stdout, options)
    else:
        console = ServerConsole(cls, options)
        console.interact()
