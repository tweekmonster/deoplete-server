import os
import struct
import threading

from deoplete.server import compat

try:
    import cPickle as pickle

    BrokenPipeError = IOError
except ImportError:
    import pickle

# Used where a return value of None still means a successful call.
Null = object()


class Reader(object):
    """Proxy file object that overrides read() to unpickle data."""

    def __init__(self, stream):
        self._stream = stream
        self._closed = False
        self._buffer = getattr(stream, 'buffer', stream)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    @property
    def closed(self):
        return self._closed

    def read(self, *n):
        """Unpickle data from the stream.

        Arguments are ignored.  Receiving less data than expected will return
        Null.
        """
        if isinstance(self._stream, Reader):
            return self._stream.read()

        b = self._buffer.read(4)
        if len(b) == 4:
            length = struct.unpack('I', b)[0]
            b = self._buffer.read(length)
            if len(b) == length:
                return pickle.loads(b)
        self._closed = True
        return Null


class Writer(object):
    """Proxy file object that overrides write() to pickle data."""

    def __init__(self, stream):
        self._stream = stream
        self._closed = False
        self._buffer = getattr(stream, 'buffer', stream)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    @property
    def closed(self):
        return self._closed

    def write(self, obj):
        if isinstance(self._stream, Writer):
            return self._stream.write(obj)

        data = pickle.dumps(obj)
        length = struct.pack('I', len(data))
        try:
            self._buffer.write(length + data)
            self._stream.flush()
        except (BrokenPipeError, ValueError):
            # BrokenPipeError is raised if the other side of the pipe is
            # closed. ValueError is raised if the current stream is closed.
            self._closed = True
            return False
        return True


def fdpipes():
    """Create a read/write pipe pair.

    File descriptors are opened before returning.
    """
    read, write = os.pipe()
    return os.fdopen(read, 'r'), os.fdopen(write, 'w')


def test_pipes():
    """Test Reader/Writer pipes.

    Used for testing and the interactive console.
    """
    read, write = fdpipes()
    return Reader(read), Writer(write)


def read_command(stream):
    """Reads a single command from the stream.

    Always returns a tuple of 3 items: cmd, cmd_id, args.
    """
    if not isinstance(stream, Reader):
        raise TypeError('Can\'t read command from %s' % type(stream))

    data = stream.read()
    if data is None:
        return None, -1, []
    elif data is Null:
        return Null, -2, []
    elif isinstance(data, compat.Text):
        return data, -3, []
    elif isinstance(data, (tuple, list)):
        if len(data) == 2:
            return data[0], -4, data[1]
        elif len(data) == 3:
            return data
        else:
            return None, -5, data
    elif not isinstance(data, (tuple, list)) or len(data) != 3:
        return None, -6, [data]

    return None, -7, [data]


def itermsg(stream):
    """Generator that reads an input for pickle payloads.

    Yields a tuple of (command, command_id, additional_args).  This is
    primarily a convenience for the server.
    """
    while True:
        cmd, cmd_id, args = read_command(stream)
        if cmd is Null:
            break
        yield cmd, cmd_id, args
