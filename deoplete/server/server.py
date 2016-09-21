import os
import sys
import site

from deoplete.server import compat, stream


def debug_info():
    startup_info = {
        'executable': sys.executable,
        'version': '.'.join(compat.Text(x) for x in sys.version_info[:3]),
        'paths': '\n'.join([x for x in sys.path if x]),
        'user_site_enabled': site.ENABLE_USER_SITE,
        'user_site': site.USER_SITE,
        'venv': os.getenv('VIRTUAL_ENV'),
    }

    return '''Server info
Executable: %(executable)s
Version: %(version)s
User Site Enabled: %(user_site_enabled)s
User Site: %(user_site)s
VirtualEnv: %(venv)s
Paths:
%(paths)s
''' % startup_info


class BaseServer(object):
    def __init__(self, log):
        self.log = log

    def configure(self, **options):
        pass

    def cmd_process(self, *args):
        return 'okay'

    def _run(self, input_stream, output_stream):
        self.log.debug(debug_info())

        for cmd, cmd_id, args in stream.itermsg(input_stream):
            try:
                if cmd == 'stop' or cmd == stream.Null:
                    break

                func = getattr(self, 'cmd_%s' % cmd, None)
                if not func:
                    self.log.warn('Unknown command: %s', cmd)
                    # Always write back.
                    out = ('noop', cmd_id, None)
                else:
                    out = ('response', cmd_id, func(self, *args))

                if not output_stream.write(out):
                    break
            except Exception:
                self.log.error('Server error.', exc_info=True)
