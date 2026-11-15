"""Keep a crawler's cleanup behind its admitted transfer and worker lifetime."""
from functools import wraps
import inspect
import subprocess
import threading
import time
from uav_downloader.core.storage import StorageWriteError


_init_lock = threading.Lock()


def transfer_lifetime(owner):
    # Some legacy callers construct crawlers with __new__ rather than __init__.
    with _init_lock:
        lifetime = getattr(owner, '_transfer_lifetime', None)
        if lifetime is None:
            lifetime = owner._transfer_lifetime = TransferLifetime(owner)
        return lifetime


class TransferLifetime:
    def __init__(self, owner):
        self.owner = owner
        self.condition = threading.Condition()
        self.active = 0
        self.cleaning = False
        self.cleanup_requested = False
        self.cleanup_thread = None
        self.cleanup_completed = False
        self.joining = False
        self.running_thread = None
        self.depth = 0

    def enter(self, *, reserved=False):
        with self.condition:
            if (getattr(self.owner, '_cancel_job', False) or self.cleaning or self.joining or
                    self.cleanup_requested):
                return False
            if reserved:
                if self.active:
                    return False
            else:
                ident = threading.get_ident()
                if self.running_thread not in (None, ident):
                    return False
                self.running_thread = ident
                self.depth += 1
            if self.active == 0:
                self.cleanup_completed = False
            self.active += 1
            return True

    def leave(self, *, reserved=False):
        with self.condition:
            if not reserved:
                self.depth -= 1
                if self.depth == 0:
                    self.running_thread = None
            self.active -= 1
            self._start_cleanup_locked()
            self.condition.notify_all()

    def request_cleanup(self):
        with self.condition:
            if (self.cleaning or self.cleanup_completed or
                    getattr(self.owner, '_storage_error', None)):
                return
            self.cleanup_requested = True
            self._start_cleanup_locked()

    def _start_cleanup_locked(self):
        if self.active or self.cleaning or self.joining or not self.cleanup_requested:
            return
        self.cleanup_requested = False
        self.cleaning = True
        self.cleanup_thread = threading.Thread(
            target=self._clean, name='uav-cache-cleanup', daemon=True)
        self.cleanup_thread.start()

    def _clean(self):
        completed = False
        try:
            if not self._join_workers(lambda: None):
                return
            # A still-running executor may report an I/O failure during join.
            if not getattr(self.owner, '_storage_error', None):
                self.owner._cleanup_temp_now()
            completed = True
        finally:
            with self.condition:
                self.cleanup_completed = completed
                self.cleaning = False
                self.condition.notify_all()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)

        def remaining():
            return None if deadline is None else max(0, deadline - time.monotonic())

        current = threading.current_thread()
        if self.running_thread == threading.get_ident() or self.cleanup_thread is current:
            return False
        for attr in ('_t2_executor', '_t_executor'):
            if current in getattr(getattr(self.owner, attr, None), '_threads', ()):
                return False
        while True:
            with self.condition:
                while self.active or self.cleaning or self.joining:
                    if remaining() == 0:
                        return False
                    self.condition.wait(remaining())
                self.joining = True
            try:
                joined = self._join_workers(remaining)
                thread = self.cleanup_thread
                if joined and thread is not None:
                    thread.join(remaining())
                    joined = not thread.is_alive()
            finally:
                with self.condition:
                    self.joining = False
                    self._start_cleanup_locked()
                    self.condition.notify_all()
            if not joined:
                return False
            with self.condition:
                if not self.active and not self.cleaning and not self.cleanup_requested:
                    return True

    def _join_workers(self, remaining):
        # A completed future alone does not establish executor-thread exit.
        for attr in ('_t2_executor', '_t_executor'):
            executor = getattr(self.owner, attr, None)
            if executor is None:
                continue
            executor.shutdown(wait=False)
            for thread in tuple(executor._threads):
                if thread is threading.current_thread():
                    return False
                thread.join(remaining())
                if thread.is_alive():
                    return False
            setattr(self.owner, attr, None)
        return True


def owned_transfer(function):
    @wraps(function)
    def run(owner, *args, **kwargs):
        lifetime = transfer_lifetime(owner)
        if not lifetime.enter():
            return False
        try:
            return function(owner, *args, **kwargs)
        except StorageWriteError as exc:
            with lifetime.condition:
                owner._storage_error = exc
                lifetime.cleanup_requested = False
            raise
        finally:
            lifetime.leave()
    return run


def cancel_and_wait(job):
    """CLI interruption preserves cache and waits when the adapter supports it."""
    cancel = getattr(job, 'cancel_download', None)
    if callable(cancel):
        try:
            inspect.signature(cancel).bind(cleanup=False)
        except TypeError:
            cancel()
        else:
            cancel(cleanup=False)
    wait = getattr(job, 'wait_stopped', None)
    if callable(wait):
        wait()


def reap_process(process):
    """Retain ownership until wait confirms exit, including failed kill/wait."""
    while process.returncode is None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.3)
            return
        except (OSError, subprocess.TimeoutExpired):
            time.sleep(0.05)
