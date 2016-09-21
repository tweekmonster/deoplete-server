"""Polling for readers and writers belonging to a server process.

The threaded poller will return data faster than the others if the server is
very fast at returning a response since the reads are I/O bound.  However, the
main thread's performance will start to degrade if there are a lot of child
threads.

epoll and kqueue are preferable since they will yield return file descriptors
when the OS knows they have data for reading.
"""
import select
import threading

from deoplete.server import stream, compat


class _ReadPoller(object):
    def __init__(self, poll_type, poller):
        self._streams = {}
        self._poll_type = poll_type
        self._poller = poller
        self._cond = compat.ThreadCondition()
        self._lock = threading.RLock()

    @property
    def poll_type(self):
        return self._poll_type

    @property
    def can_poll(self):
        return len(self._streams) > 0

    def _cleanup(self):
        raise NotImplemented

    def _poll(self, timeout):
        raise NotImplemented

    def poll(self, timeout=None):
        self._cleanup()
        return self._poll(timeout)

if hasattr(select, 'epoll'):
    class ReadPoller(_ReadPoller):
        def __init__(self):
            super(ReadPoller, self).__init__('epoll', select.epoll())

        def register(self, obj):
            self._streams[obj.fileno()] = obj
            self._poller.register(obj, select.EPOLLIN | select.EPOLLPRI)

        def _cleanup(self):
            for fileno in list(self._streams.keys()):
                if self._streams[fileno].closed:
                    self._poller.unregister(fileno)
                    self._streams.pop(fileno)

        def _poll(self, timeout):
            if timeout is None:
                timeout = -1

            for fd, event in self._poller.poll(timeout):
                s = self._streams.get(fd)
                if s:
                    yield s

elif False and hasattr(select, 'kqueue'):
    class ReadPoller(_ReadPoller):
        def __init__(self):
            super(ReadPoller, self).__init__('kqueue', select.kqueue())
            self._kevents = []

        def register(self, obj):
            k = select.kevent(obj.fileno(), filter=select.KQ_FILTER_READ,
                              flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE)
            self._kevents.append(k)
            self._streams[obj.fileno()] = obj

        def _cleanup(self):
            closed = []
            for fileno in list(self._streams.keys()):
                if self._streams[fileno].closed:
                    self._streams.pop(fileno)
                    closed.append(fileno)

            self._kevents[:] = [x for x in self._kevents
                                if x.ident not in closed]

        def _poll(self, timeout):
            for event in self._poller.control(self._kevents,
                                              len(self._kevents), timeout):
                s = self._streams.get(event.ident)
                if s:
                    yield s
else:
    _ReaderBusy = object()

    class _ThreadedReader(threading.Thread, stream.Reader):
        """A non-blocking stream reader that's 'good enough'.

        Depends on thread conditions.  When 'busy', the stream is blocking and
        a call to read() will return a Null value.
        """

        def __init__(self, poller, fd):
            threading.Thread.__init__(self)
            self.daemon = True

            stream.Reader.__init__(self, fd)
            self._fileno = self._stream.fileno()
            self._poller = poller
            self._data = _ReaderBusy
            self._read_cond = threading.Condition()

        def fileno(self):
            return self._fileno

        def run(self):
            self._data = _ReaderBusy

            while self._read_cond is not None:
                with self._read_cond:
                    self._data = stream.Reader.read(self)
                    if self._data is stream.Null:
                        break

                    with self._poller._cond:
                        self._poller._cond.notify()
                    self._read_cond.wait()
                    self._data = _ReaderBusy

            self._read_cond = None

        @property
        def closed(self):
            """Check if the stream is closed.

            Not considered closed until the unread data is "unset".
            """
            return self._read_cond is None and self._data is _ReaderBusy

        @property
        def busy(self):
            busy = True
            if self._read_cond is not None and self._read_cond.acquire(False):
                busy = self._data is _ReaderBusy
                self._read_cond.release()
            return busy

        def read(self):
            ret = stream.Null
            if self._read_cond is None:
                # If there's unread data, return it now.
                ret = self._data
                self._data = stream.Null
                self._busy = False
            elif self._read_cond.acquire(False):
                if self._data is not _ReaderBusy:
                    ret = self._data
                    self._busy = True
                    self._data = _ReaderBusy
                    if self._read_cond is not None:
                        self._read_cond.notify()
                if self._read_cond is not None:
                    self._read_cond.release()
            return ret

    class ReadPoller(_ReadPoller):
        def __init__(self):
            super(ReadPoller, self).__init__('threading', None)

        def register(self, obj):
            t = _ThreadedReader(self, obj)
            t.start()
            self._streams[obj.fileno()] = t

        def _cleanup(self):
            with self._lock:
                for fileno in list(self._streams.keys()):
                    if self._streams[fileno].closed:
                        self._streams.pop(fileno)

        def _non_busy(self):
            with self._lock:
                return [x for x in self._streams.values() if not x.busy]

        def _poll(self, timeout):
            result = []

            if timeout is None:
                while not result:
                    with self._cond:
                        result = self._cond.wait_for(self._non_busy, 0.01)
            else:
                with self._cond:
                    result = self._cond.wait_for(self._non_busy, timeout)

            for t in result:
                yield t
