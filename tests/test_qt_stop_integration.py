"""Exercise the Qt service against the shared cooperative download owner."""
from pathlib import Path
import threading

from uav_downloader.core import task_queue
from uav_downloader.core.stop import StopReason, current_stop_context
from uav_downloader.core.task_service import TaskService


class ControlledDownload:
    def __init__(self, entered, release):
        self.context = current_stop_context()
        self.entered = entered
        self.release = release

    @property
    def _cancel_job(self):
        return self.context.requested.is_set()

    def is_url_vaildate(self):
        return True

    def target_name(self):
        return 'Controlled Qt task'

    def start_download(self):
        self.entered.set()
        assert self.release.wait(5)
        return False

    def cancel_download(self, cleanup=True):
        self.context.request(self.context.reason or StopReason.CANCEL, cleanup=cleanup)


def test_qt_close_keeps_worker_owned_until_exit_then_saves_incomplete(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    jobs = []

    def create(*args, **kwargs):
        job = ControlledDownload(entered, release)
        jobs.append(job)
        return job

    monkeypatch.setattr(task_queue.M3U8Sites, 'CreateSite', create)
    manager = task_queue.DownloadManager(
        subtitle_mode_getter=lambda: 'none', slow_requeue_kbps_getter=lambda: 0)
    service = TaskService(str(tmp_path / 'queue.csv'), manager=manager,
                          initialize_resolution=False)
    try:
        service.ready.result(3)
        url = 'https://jable.tv/videos/controlled-qt-close/'
        service.submit('enqueue', url, str(tmp_path)).result(3)
        assert entered.wait(2)
        closing = service.close()
        assert jobs[0].context.requested.wait(2)
        assert jobs[0].context.reason == StopReason.SHUTDOWN
        assert manager.active_count == 1
        assert not closing.done()
        release.set()
        assert closing.result(3)
        assert manager.wait_stopped(timeout=1)
        saved = Path(service.path).read_text(encoding='utf-8-sig')
        assert url in saved and '未完成' in saved
    finally:
        release.set()
        service.close().result(3)


def test_qt_listing_fetch_inherits_stop_and_late_batch_cannot_requeue(tmp_path):
    from uav_downloader.core.listing_pool import ListingPool

    entered, release = threading.Event(), threading.Event()
    contexts, batches = [], []

    def fetch(*args):
        context = current_stop_context()
        contexts.append(context)
        entered.set()
        assert release.wait(5)
        return [dict(url='https://jable.tv/videos/late-qt-listing/')]

    pool = ListingPool(lambda request, batch: batches.append(batch),
                       lambda *args: None, fetcher=fetch, workers=1)
    request = pool.start('JableTV', 'https://jable.tv/latest-updates/', str(tmp_path), 0)
    try:
        assert entered.wait(2)
        pool.stop_all(close=True)
        assert contexts[0] is request.stop
        assert contexts[0].requested.is_set()
        assert not request.stop.finished.is_set()
        release.set()
        assert pool.wait_stopped(timeout=3)
        assert request.done.result(1) == 0
        assert request.stop.finished.is_set()
        assert batches == []
    finally:
        release.set()
        pool.stop_all(close=True)
        pool.wait_stopped(timeout=3)
