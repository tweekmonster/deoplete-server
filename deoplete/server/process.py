import os
import json
import logging
import threading
import subprocess

from deoplete.server import compat, stream
from deoplete.server.polling import ReadPoller

log = logging.getLogger('deoplete.server')

_command_id = 0
_process_id = 0
_process_manager_id = 0


class Process(object):
    """Server process.

    This class makes assumptions based on a well-behaved server.  The server
    must always read from stdin and exit when stdin no longer provides data.

    The subprocess's pipes are not managed by this class.  The only pipe
    interaction this class has is closing stdin when stopping.

    `server_module` is the module that can be found in `sys.path` and ran from
    the command line with Python's -m flag.

    The `busy` attribute is managed by the ProcessManager class.
    """

    def __init__(self, server_module, log_level, **options):
        self.busy = False

        global _process_id
        self._id = _process_id
        _process_id += 1

        self.server_module = server_module
        self.log_level = log_level

        self.executable = options.pop('executable', None)

        self.paths = options.pop('paths', [])
        assert isinstance(self.paths, (list, tuple))

        self.env = options.pop('env', {})
        assert isinstance(self.env, dict)

        self.options = options

    def start(self):
        executable = self.executable

        if not executable:
            venv = os.getenv('VIRTUALENV')
            if venv and os.path.exists(os.path.join(venv, 'bin/python')):
                executable = os.path.exists(os.path.join(venv, 'bin/python'))
            else:
                executable = 'python'

        cmd = [executable, '-u', '-m', self.server_module, '--log-level',
               str(self.log_level)]

        env = os.environ.copy()

        # User-defined paths can be set from an existing PYTHONPATH variable,
        # but it would be preferred if you gave them another way to set
        # additional paths.
        paths = env.get('PYTHONPATH', '').split(os.pathsep)
        for p in reversed(self.paths):
            if p not in paths:
                paths.insert(0, os.path.abspath(p))
        env['PYTHONPATH'] = os.pathsep.join(paths)

        # This variable is where server-specific configs can be found.  It also
        # tells the deoplete.server.run_server() function that the server is
        # not being ran from the command line.
        env['DEOPLETE_SERVER'] = json.dumps(self.options)

        self._proc = subprocess.Popen(cmd, env=env,
                                      stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
        self.stdin = stream.Writer(self._proc.stdin)
        self.stdout = stream.Reader(self._proc.stdout)
        self.stderr = stream.Reader(self._proc.stderr)

    def owns_stream(self, fileno):
        if hasattr(fileno, 'fileno'):
            fileno = fileno.fileno()
        return any(x.fileno() == fileno
                   for x in (self.stdin, self.stdout, self.stderr))

    def stop(self):
        """Stops the subprocess.

        If it does not return a status code within one second, terminate the
        process.
        """
        self._proc.stdin.close()
        if self._proc.wait(1) is None:
            self._proc.terminate()
        return self._proc.poll()


class ProcessResponse(object):
    def __init__(self, manager, cmd_id, proc):
        self._manager = manager
        self._cmd_id = cmd_id
        self._proc = proc


class ProcessManager(object):
    """Manages spawned processes.

    The stderr stream of all processes are watched by a thread.
    """
    def __init__(self, min_procs, max_procs, server_module, **server_options):
        global _process_manager_id
        self._id = _process_manager_id
        _process_manager_id += 1
        self._log = log.getChild('pm(%d)' % self._id)

        self.min_procs = min_procs
        self.max_procs = max_procs
        self.server_module = server_module
        self.server_options = server_options

        self._write_cond = compat.ThreadCondition()
        self._procs = []
        self._pending = []

        self._stderr_lock = threading.RLock()
        self._stderr_thread = None
        self._stdout = ReadPoller()
        self._stderr = ReadPoller()
        self._log.debug('stderr poller: %s', self._stderr.poll_type)

    def spawn(self):
        p = Process(self.server_module, log.level, **self.server_options)
        p.start()
        self._stdout.register(p.stdout)
        self._procs.append(p)
        self._add_stderr(p.stderr)
        return p

    def available_proc(self):
        while len(self._procs) < self.min_procs:
            self.spawn()

        for p in self._procs:
            if not p.busy:
                return p

        if len(self._procs) < self.max_procs:
            return self.spawn()

    def writable(self):
        return len(self._procs) < self.max_procs \
            or any([not x.busy for x in self._procs])

    def poll_procs(self):
        for s in self._stdout.poll(0):
            self._log.debug('Reply: %s', s.read())

    def communicate(self, command, *args):
        self.poll_procs()

        with self._write_cond:
            self._write_cond.wait_for(self.writable)

        global _command_id
        _command_id += 1
        p = self.available_proc()
        p.stdin.write((command, _command_id, args))
        return _command_id

    def _add_stderr(self, stderr):
        self._stderr.register(stderr)
        self._start_stderr_thread()

    def _start_stderr_thread(self):
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            return
        self._log.debug('Starting stderr reader thread.')
        t = threading.Thread(target=self._stderr_read_thread)
        t.daemon = True
        t.start()
        self._stderr_thread = t

    def _stderr_read(self, stderr):
        proc_id = '?'
        search_fd = stderr._stream.fileno()
        for p in self._procs:
            if p.owns_stream(search_fd):
                proc_id = p._id
                break

        log = self._log.getChild('proc(%s)' % proc_id)
        cmd, cmd_id, args = stream.read_command(stderr)
        try:
            if cmd == 'log':
                record = args[0]
                message = args[1]
                log.log(record.levelno, message)
            elif cmd is stream.Null:
                # Died
                log.error('Stream is closed')
            else:
                log.warn('Unknown command on stderr: %s, id: %r, args: %r',
                         cmd, cmd_id, args)
        except Exception:
            self._log.error('Error reading stderr', exc_info=True)

    def _stderr_read_thread(self):
        while self._stderr.can_poll:
            for stderr in self._stderr.poll():
                self._stderr_read(stderr)
