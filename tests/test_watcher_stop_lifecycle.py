"""Watcher cancellation reaches parsing and shutdown waits for real exit."""
import threading

import pytest

from uav_downloader.apps import watch as watcher
from uav_downloader.core.stop import StopReason, StopTimeoutError, current_stop_context


def worker_for(tmp_path):
    watcher.load_seen = lambda: {}
    return watcher.SmallToolWorker(lambda _: None)


def test_stop_reaches_site_factory_before_active_site_is_registered(tmp_path, monkeypatch):
    worker = worker_for(tmp_path)
    entered, release = threading.Event(), threading.Event()
    contexts, errors = [], []
    class Invalid:
        def is_url_vaildate(self):
            return False
    def create(*args):
        contexts.append(current_stop_context())
        entered.set()
        assert release.wait(3)
        return Invalid()
    monkeypatch.setattr(watcher.M3U8Sites, 'CreateSite', create)
    def scan(cfg):
        try:
            worker._download_one({'url': 'https://jable.tv/videos/stop-parser/'}, str(tmp_path))
        except BaseException as exc:
            errors.append(exc)
        return True
    worker._scan_and_download = scan
    try:
        assert worker.start_once() and entered.wait(2)
        worker.stop()
        assert contexts[0] is not None and contexts[0].requested.is_set()
        assert not worker.wait_until_stopped(timeout=0.03)
        release.set()
        assert worker.wait_until_stopped(timeout=2)
        assert not errors and worker._seen == {}
        assert contexts[0].finished.is_set()
    finally:
        release.set()
        worker.stop()
        worker.wait_until_stopped(timeout=3)


def test_stop_between_video_and_subtitles_does_not_start_new_work_or_mark_seen(tmp_path, monkeypatch):
    worker = worker_for(tmp_path)
    subtitles = []
    class Job:
        _cancel_job = False
        def is_url_vaildate(self):
            return True
        def start_download(self):
            worker.stop()
            return True
        def _get_video_savename(self):
            return str(tmp_path/'video.mp4')
    monkeypatch.setattr(watcher.M3U8Sites, 'CreateSite', lambda *a: Job())
    monkeypatch.setattr(watcher, 'generate_subtitles', lambda *a, **kw: subtitles.append(True))
    worker._download_one({'url': 'https://jable.tv/videos/stop-stage/'}, str(tmp_path), subtitle_mode='ja')
    assert subtitles == [] and worker._seen == {}




def test_watcher_stop_keeps_storage_failure_preservation_intent(tmp_path):
    worker = worker_for(tmp_path)
    cleanup_calls = []
    class Job:
        def cancel_download(self, cleanup=True):
            cleanup_calls.append(cleanup)
    worker._active_site = Job()
    worker._halt_for_storage_error(OSError('controlled device error'))
    worker.stop()
    worker.cancel_active_download()
    assert cleanup_calls == [False]
    assert worker._run_stop.reason == StopReason.STORAGE_ERROR
    assert not worker._run_stop.cleanup_requested


def test_each_watcher_run_uses_a_new_context_and_retires_the_old_one(tmp_path):
    worker = worker_for(tmp_path)
    contexts = []
    def scan(cfg):
        contexts.append(current_stop_context())
        worker.stop()
        return True
    worker._scan_and_download = scan
    for _ in range(2):
        assert worker.start_once()
        assert worker.wait_until_stopped(timeout=2)
    assert all(context is not None and context.finished.is_set() for context in contexts)
    assert len(contexts) == 2 and contexts[0] is not contexts[1]
