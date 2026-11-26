"""Exercise the public stop path inside a real executor's submission window."""
import concurrent.futures
import errno
import threading

import pytest

from uav_downloader.core.storage import StorageWriteError
from uav_downloader.sites import base


def _job():
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._tsList = ["offline-a", "offline-b"]
    job._pending_set = set(enumerate(job._tsList))
    job._max_workers = 1
    job._segment_retry_rounds = 1
    job.respect_segment_denials = False
    job._cancel_job = False
    job._t_executor = job._t2_executor = None
    return job


@pytest.mark.parametrize("disk_failure", [False, True])
def test_stop_during_submission_waits_for_running_writer(monkeypatch, tmp_path, disk_failure):
    job = _job()
    writing = threading.Event()
    submitting_next = threading.Event()
    stopped = threading.Event()
    retired = threading.Event()
    disk = StorageWriteError(str(tmp_path), OSError(errno.EIO, "offline fixture"))
    ran = []

    class GatedPool(concurrent.futures.ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            if getattr(self, "submitted_one", False):
                submitting_next.set()
                assert stopped.wait(5)
            self.submitted_one = True
            return super().submit(fn, *args, **kwargs)

    def write(task):
        if job._cancel_job:
            return
        ran.append(task)
        writing.set()
        assert stopped.wait(5)
        try:
            if disk_failure:
                job._storage_error = disk  # _scrape records the real I/O exception
                raise disk
        finally:
            retired.set()

    def stop():
        try:
            if writing.wait(5) and submitting_next.wait(5):
                job.cancel_download(cleanup=False)
        finally:
            stopped.set()

    monkeypatch.setattr(base.concurrent.futures, "ThreadPoolExecutor", GatedPool)
    job._scrape = write
    thread = threading.Thread(target=stop)
    thread.start()
    try:
        if disk_failure:
            with pytest.raises(StorageWriteError) as caught:
                job._startCrawl()
            assert caught.value is disk
        else:
            job._startCrawl()
    finally:
        thread.join(6)
    assert not thread.is_alive()
    assert job._cancel_job and retired.is_set() and len(ran) == 1
    assert job._t2_executor is None


@pytest.mark.parametrize(("user_stop", "fault"), [
    (False, concurrent.futures.CancelledError()),
    (False, RuntimeError("worker fault")),
    (True, RuntimeError("cannot schedule new futures after shutdown")),
])
def test_unexpected_worker_exception_releases_executor_without_hiding_error(user_stop, fault):
    job = _job()

    def fail(_):
        job._cancel_job = user_stop
        raise fault

    job._scrape = fail
    with pytest.raises(type(fault)) as caught:
        job._startCrawl()
    assert caught.value is fault
    assert job._t2_executor is None


def test_stop_before_executor_publication_does_not_queue_a_large_playlist(monkeypatch):
    job = _job()
    job._storage_error = None
    submitted = []

    class StopBeforePublish(concurrent.futures.ThreadPoolExecutor):
        def __enter__(self):
            job.cancel_download(cleanup=False)
            return super().__enter__()

        def submit(self, fn, *args, **kwargs):
            submitted.append(1)
            return super().submit(fn, *args, **kwargs)

    monkeypatch.setattr(base.concurrent.futures, "ThreadPoolExecutor", StopBeforePublish)
    job._scrape = lambda task: None
    job._run_segment_round(range(2_000))
    assert len(submitted) == 0
