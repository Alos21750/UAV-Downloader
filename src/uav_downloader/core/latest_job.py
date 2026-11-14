"""A bounded background worker for replaceable listing/filter requests."""
import threading
from concurrent.futures import Future


class LatestJob:
    def __init__(self):
        self._condition = threading.Condition()
        self._pending = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='latest-request', daemon=True)
        self._thread.start()

    def submit(self, operation):
        future = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError('Worker closed')
            old, self._pending = self._pending, (operation, future)
            self._condition.notify()
        if old is not None:
            old[1].cancel()
        return future

    def close(self):
        with self._condition:
            self._closed = True
            old, self._pending = self._pending, None
            self._condition.notify()
        if old is not None:
            old[1].cancel()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                operation, future = self._pending
                self._pending = None
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(operation())
            except Exception as error:
                future.set_exception(error)
