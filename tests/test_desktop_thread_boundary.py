"""Regressions for responsiveness when backend commands or persistence stall."""
from dataclasses import FrozenInstanceError
import threading

import pytest

from uav_downloader.core.task_queue import DownloadManager
from uav_downloader.core.task_service import TaskService


def test_gui_snapshot_does_not_wait_for_busy_queue_and_remains_immutable():
    manager = DownloadManager()
    item = manager.add_item('https://jable.tv/videos/test/', state='下載中')
    previous = manager.snapshot_items()
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    def command():
        with manager._lock:
            item.progress = 50
            entered.set()
            release.wait(3)

    worker = threading.Thread(target=command)
    worker.start()
    try:
        assert entered.wait(1)
        result = []
        reader = threading.Thread(target=lambda: (result.append(manager.snapshot_items()), returned.set()))
        reader.start()
        assert returned.wait(.5), 'UI snapshot waited on a blocked backend'
        reader.join(1)
        assert result == [previous] and previous[0].progress == 0
        with pytest.raises(FrozenInstanceError):
            previous[0].progress = 75
    finally:
        release.set()
        worker.join(1)
    assert manager.snapshot_items()[0].progress == 50


def test_slow_save_is_a_future_and_close_preserves_order(tmp_path, monkeypatch):
    import uav_downloader.core.queue_store as store
    entered, release = threading.Event(), threading.Event()
    original = store.write_csv_snapshot

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, 'write_csv_snapshot', blocked)
    path = tmp_path / 'queue.csv'
    service = TaskService(str(path), DownloadManager())
    try:
        service.ready.result(2)
        service.manager.add_item('https://jable.tv/videos/test/', state='等待中')
        saved = service.submit('save')
        assert entered.wait(1) and not saved.done()
        closing = service.close()
        assert not closing.done()
        release.set()
        assert saved.result(2) is True
        assert closing.result(2) is True
        restored = DownloadManager()
        restored.load_csv(path)
        assert restored.contains('https://jable.tv/videos/test/')
    finally:
        release.set()
        service.abandon()
