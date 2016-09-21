try:
    Text = unicode
except NameError:
    Text = str

try:
    from time import monotonic as time  # noqa
except ImportError:
    from time import time  # noqa

import sys

if sys.version_info[:2] < (3, 2):
    # Backport thread.Condition
    import threading

    if hasattr(threading, '_Condition'):
        BaseCondition = threading._Condition
    else:
        BaseCondition = threading.Condition

    class ThreadCondition(BaseCondition):
        def wait_for(self, predicate, timeout=None):
            """Wait until a condition evaluates to True.

            predicate should be a callable which result will be interpreted as a
            boolean value.  A timeout may be provided giving the maximum time to
            wait.

            """
            endtime = None
            waittime = timeout
            result = predicate()
            while not result:
                if waittime is not None:
                    if endtime is None:
                        endtime = time() + waittime
                    else:
                        waittime = endtime - time()
                        if waittime <= 0:
                            break
                threading.Condition.wait(self, waittime)
                result = predicate()
            return result
else:
    from threading import Condition as ThreadCondition  # noqa
