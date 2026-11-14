"""Immutable CSV snapshots and a single, revision-aware background writer."""
import csv
import os
import tempfile
import threading
from concurrent.futures import Future


def write_csv_snapshot(path, rows, publish=None):
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.uav-queue-', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            csv.writer(handle).writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        if publish is not None:
            return publish(temporary, path)
        os.replace(temporary, path)
        return True
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


class QueueSnapshotWriter:
    """Coalesce pending snapshots; publications always follow revision order.

    submit() is nonblocking. Its Future resolves True after atomic publication,
    False when superseded, or raises the disk error. UI callers poll the Future;
    the worker never touches Tk. close() does not join on the calling thread.
    """

    def __init__(self, path):
        self._path = path
        self._condition = threading.Condition()
        self._revision = 0
        self._pending = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='queue-save', daemon=True)
        self._thread.start()

    def submit(self, rows):
        snapshot = tuple(tuple(row) for row in rows)
        future = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError('Queue writer is closed')
            self._revision += 1
            old = self._pending
            self._pending = (self._revision, snapshot, future)
            self._condition.notify()
        if old is not None and old[2].set_running_or_notify_cancel():
            old[2].set_result(False)
        return future

    def close(self, discard=False):
        with self._condition:
            self._closed = True
            old = None
            if discard:
                self._revision += 1
                old, self._pending = self._pending, None
            self._condition.notify()
        if old is not None and old[2].set_running_or_notify_cancel():
            old[2].set_result(False)

    def wait_closed(self, timeout=None):
        """For non-GUI callers/tests; UI shutdown instead polls its final Future."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._pending is None:
                    return
                revision, rows, future = self._pending
                self._pending = None
            if not future.set_running_or_notify_cancel():
                continue

            def publish(temporary, path):
                with self._condition:
                    if revision != self._revision:
                        return False
                # Replacement can itself be slow on a network volume. Do not
                # hold the submit lock during I/O. An already-started replace
                # may finish, but this sole worker publishes the newer revision
                # afterwards, never in the opposite order.
                os.replace(temporary, path)
                return True

            try:
                result = write_csv_snapshot(self._path, rows, publish)
            except Exception as error:
                future.set_exception(error)
            else:
                future.set_result(result)
