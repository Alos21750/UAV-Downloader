"""Cooperative stop intent shared by parsing, transfer and worker admission."""
from contextlib import contextmanager
from enum import Enum
import threading
import time


class StopReason(str, Enum):
    RETRY = 'retry'
    PAUSE = 'pause'
    SHUTDOWN = 'shutdown'
    CANCEL = 'cancel'
    STORAGE_ERROR = 'storage-error'


class DownloadStopped(Exception):
    """An expected stop at an I/O boundary, not a retryable network failure."""


class StopTimeoutError(TimeoutError):
    """Work is still stopping; ownership must not be declared released."""


class StopContext:
    POLL_SECONDS = 0.05
    _PRIORITY = {reason: index for index, reason in enumerate(StopReason)}

    def __init__(self, *, managed=False):
        self.requested = threading.Event()
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._reason = None
        self._cleanup = False
        self.managed = managed
        self._active = 0
        self._signal_reason = None

    @property
    def active_count(self):
        with self._lock:
            return self._active

    @contextmanager
    def activity(self):
        with self._lock:
            if self.managed and self.finished.is_set():
                raise DownloadStopped('This worker context has already retired')
            self.finished.clear()
            self._active += 1
        try:
            yield self
        finally:
            with self._lock:
                self._active -= 1

    def finish(self):
        """Only an owner which disposed all I/O/cleanup may acknowledge exit."""
        with self._lock:
            if self._active:
                return False
            self.finished.set()
            return True

    @property
    def reason(self):
        with self._lock:
            return self._reason or (StopReason.CANCEL if self.requested.is_set() else None)

    @property
    def cleanup_requested(self):
        with self._lock:
            return self._cleanup

    def request(self, reason=StopReason.CANCEL, *, cleanup=False):
        reason = StopReason(reason)
        with self._lock:
            if self._reason is None or self._PRIORITY[reason] > self._PRIORITY[self._reason]:
                self._reason = reason
            self._cleanup = (self._cleanup or bool(cleanup)) and self._reason != StopReason.STORAGE_ERROR
            self.requested.set()

    def check(self):
        self._flush_signal()
        if self.requested.is_set():
            raise DownloadStopped((self.reason or StopReason.CANCEL).value)

    def request_from_signal(self, reason=StopReason.SHUTDOWN):
        # A Python signal handler can interrupt this very thread while it
        # holds _lock or Event's condition. Only latch a reference here; the
        # owner/check boundary or console notifier performs normal locking.
        self._signal_reason = reason

    def _flush_signal(self):
        reason = self._signal_reason
        if reason is not None:
            self.request(reason)
            self._signal_reason = None

    def wait(self, seconds, cancel_check=None):
        """Return True on stop, False after the delay; no sleeping past stop."""
        seconds = max(0.0, seconds)
        if cancel_check is None:
            return self.requested.wait(seconds)
        deadline = time.monotonic() + seconds
        while not self.requested.is_set():
            if cancel_check():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self.requested.wait(min(self.POLL_SECONDS, remaining))
        return True

    def acquire(self, semaphore, cancel_check=None):
        """Acquire only while live; a stopped waiter never retains a permit."""
        def stopped():
            return self.requested.is_set() or (cancel_check is not None and cancel_check())
        while not stopped():
            if semaphore.acquire(timeout=self.POLL_SECONDS):
                if stopped():
                    semaphore.release()
                    return False
                return True
        return False


_local = threading.local()


def current_stop_context():
    return getattr(_local, 'context', None)


@contextmanager
def stop_scope(context):
    """Site constructors inherit the caller's stop without changing v2 factories."""
    previous = current_stop_context()
    _local.context = context
    try:
        yield context
    finally:
        _local.context = previous


@contextmanager
def execution_scope(context):
    try:
        with context.activity(), stop_scope(context):
            yield context
    finally:
        if not context.managed:
            context.finish()


@contextmanager
def console_stop_scope(context):
    """Request stop before Ctrl+C can unwind a pool's blocking __exit__."""
    import signal
    installed = threading.current_thread() is threading.main_thread()
    previous = None
    notifier = None
    notifier_done = threading.Event()
    if installed:
        previous = signal.getsignal(signal.SIGINT)
        def forward():
            while not notifier_done.wait(0.01):
                context._flush_signal()
        notifier = threading.Thread(target=forward, name='console-stop-notifier', daemon=True)
        notifier.start()
        signal.signal(signal.SIGINT, lambda signum, frame: context.request_from_signal())
    try:
        with stop_scope(context):
            yield context
    finally:
        if installed:
            signal.signal(signal.SIGINT, previous)
            notifier_done.set()
            notifier.join()
            context._flush_signal()
