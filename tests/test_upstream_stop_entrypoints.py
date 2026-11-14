"""Public entry points retain stop ownership until actual retirement."""
from types import SimpleNamespace
import threading
from concurrent.futures import Future

from uav_downloader.apps import browse
from uav_downloader.cli import headless
from uav_downloader.core.stop import StopContext, StopReason, DownloadStopped
from uav_downloader.sites import huangguo
import pytest


def test_cli_keyboard_interrupt_preserves_cache_and_waits_for_job(tmp_path, monkeypatch):
    calls = []
    class Job:
        def is_url_vaildate(self): return True
        def start_download(self): raise KeyboardInterrupt()
        def cancel_download(self, cleanup=True): calls.append(('cancel', cleanup))
        def wait_stopped(self): calls.append(('wait',)); return True
    monkeypatch.setattr(headless.M3U8Sites, 'CreateSite', lambda *a, **kw: Job())
    assert headless.main(['-o', str(tmp_path), 'https://jable.tv/videos/controlled/']) == 130
    assert calls == [('cancel', False), ('wait',)]


def test_browser_shutdown_waits_off_ui_and_saves_after_retirement(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def wait(timeout):
        entered.set()
        return release.wait(timeout)
    manager = SimpleNamespace(cancel_all=lambda **kw: calls.append(('stop', kw)),
        wait_stopped=wait, save_csv=lambda path: calls.append(('save', path)))
    app = SimpleNamespace(_dlmgr=manager, _close_listings=())
    attempt = browse.ModernApp._start_close_work(app)
    try:
        assert entered.wait(2)
        assert not attempt.future.done()
        assert not any(call[0] == 'save' for call in calls)
        release.set()
        assert attempt.future.result(2)
        assert calls[0] == ('stop', {'cleanup': False, 'reason': StopReason.SHUTDOWN})
        assert calls[-1][0] == 'save'
        assert attempt.wait(2)
    finally:
        release.set()
        attempt.wait(2)


def test_huangguo_cache_hit_cannot_ignore_inherited_stop():
    from uav_downloader.core.stop import stop_scope
    context = StopContext()
    context.request(StopReason.SHUTDOWN)
    with stop_scope(context), pytest.raises(DownloadStopped):
        huangguo._check_control()


def test_shutdown_keeps_running_and_queued_rows_resumable(monkeypatch):
    manager = browse.DownloadManager(max_concurrent=1)
    monkeypatch.setattr(manager, '_start_download_thread', lambda task: None)
    for suffix in ('one', 'two'):
        manager.enqueue('https://jable.tv/videos/' + suffix + '/', 'download')
    manager.cancel_all(cleanup=False, reason=StopReason.SHUTDOWN)
    assert manager.wait_stopped(timeout=1)
    assert [item.state for item in manager.get_items()] == ['未完成', '未完成']


@pytest.mark.parametrize('startup_complete', [False, True])
def test_repeated_browser_close_starts_one_shutdown(monkeypatch, startup_complete):
    calls = []
    app = SimpleNamespace(
        _startup_complete=startup_complete,
        _dl_drain_id=None,
        _thumb_executor=SimpleNamespace(shutdown=lambda **kwargs: None),
        _listing_requests=(),
    )
    monkeypatch.setattr(browse.ModernApp, '_stop_listing_requests', lambda app: None)

    def start_close(app):
        calls.append('start')
        return SimpleNamespace(future=Future())

    monkeypatch.setattr(browse.ModernApp, '_start_close_work', start_close)
    monkeypatch.setattr(browse.ModernApp, '_poll_close_work',
                        lambda *args: calls.append('poll'))
    browse.ModernApp._on_close(app)
    if not startup_complete:
        assert app._close_requested
        assert calls == []
        app._startup_complete = True
    browse.ModernApp._on_close(app)
    browse.ModernApp._on_close(app)
    assert calls == ['start', 'poll']


def test_browser_save_failure_allows_one_explicit_close_retry(monkeypatch):
    failed = Future()
    failed.set_exception(OSError('Controlled save failure'))
    retried = Future()
    starts, polls = [], []
    app = SimpleNamespace(_close_save_pending=True,
                          after=lambda delay, callback: polls.append(callback))
    monkeypatch.setattr(browse.messagebox, 'askretrycancel', lambda *args: True)

    def start_close(app):
        starts.append('retry')
        return SimpleNamespace(future=retried)

    monkeypatch.setattr(browse.ModernApp, '_start_close_work', start_close)
    browse.ModernApp._poll_close_work(app, failed, 0)
    browse.ModernApp._on_close(app)
    assert starts == ['retry']
    assert len(polls) == 1
    assert app._close_save_pending
