"""Background shutdown attempts used by the compatibility Tk windows."""
from concurrent.futures import Future
import threading
import time

from uav_downloader.core.stop import StopTimeoutError


class ShutdownAttempt:
    def __init__(self, operation, timeout=10.0):
        self.future = Future()
        self.thread = threading.Thread(target=self._run, args=(operation, timeout),
                                       name='compat-shutdown', daemon=True)
        self.thread.start()

    def _run(self, operation, timeout):
        if not self.future.set_running_or_notify_cancel():
            return
        deadline = time.monotonic() + timeout
        try:
            self.future.set_result(operation(deadline))
        except BaseException as error:
            self.future.set_exception(error)

    def wait(self, timeout=None):
        self.thread.join(timeout)
        return not self.thread.is_alive()


def remaining(deadline):
    return max(0.0, deadline - time.monotonic())


def require_stopped(waiter, deadline):
    if not waiter(timeout=remaining(deadline)):
        raise StopTimeoutError('Background work is still stopping; retry closing after I/O exits')
