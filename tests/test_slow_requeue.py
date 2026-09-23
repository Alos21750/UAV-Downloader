import json
import sys
import threading
import types


def _stub_runtime_dependency(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


def _cloudscraper_stub():
    mod = types.ModuleType('cloudscraper')
    mod.create_scraper = lambda *args, **kwargs: None
    return mod


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
    return mod


def _customtkinter_stub():
    mod = types.ModuleType('customtkinter')

    class CTk:
        pass

    mod.CTk = CTk
    mod.CTkLabel = CTk
    return mod


_stub_runtime_dependency('cloudscraper', _cloudscraper_stub)
_stub_runtime_dependency('m3u8', _m3u8_stub)
_stub_runtime_dependency('customtkinter', _customtkinter_stub)

from uav_downloader.core import config
from uav_downloader.apps import browse as gui_modern
from uav_downloader.apps.browse import (
    DownloadManager,
    SLOW_REQUEUE_GRACE_SECONDS,
    SLOW_REQUEUE_MAX_ATTEMPTS,
    _DownloadTask,
)
from uav_downloader.i18n.locales import T
from uav_downloader.sites import base as sites_base


URL = 'https://supjav.com/slow-requeue.html'
DEST = r'C:\Videos'
SLOW_BPS = 500 * 1024      # 500 KB/s
THRESHOLD_KBPS = 750       # KB/s


def _active_task(manager, now):
    """Register a fresh, already-active task for URL and return it."""
    task = _DownloadTask(URL, DEST, manager._cancel_epoch)
    task.download_started = now
    with manager._lock:
        manager._active[URL] = task
    return task


def test_slow_after_grace_triggers_one_restart_and_increments_counter(
        monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    manager = DownloadManager(
        max_concurrent=1, slow_requeue_kbps_getter=lambda: THRESHOLD_KBPS)
    item = manager.add_item(URL, dest=DEST, state='下載中')
    task = _active_task(manager, now[0])

    restart_calls = []
    restart_done = threading.Event()

    def fake_restart(url, dest, automatic=False):
        restart_calls.append((url, dest, automatic))
        restart_done.set()
        return True

    monkeypatch.setattr(manager, 'restart', fake_restart)

    now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
    manager._on_context_progress(task, 1, 100, SLOW_BPS)

    assert restart_done.wait(1)
    assert restart_calls == [(URL, DEST, True)]
    assert item.auto_requeues == 1
    assert task.slow_requeue_triggered is True

    # A second slow tick on the SAME task must not fire a second restart.
    manager._on_context_progress(task, 2, 100, SLOW_BPS)
    assert restart_calls == [(URL, DEST, True)]


def test_no_restart_when_threshold_disabled(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    manager = DownloadManager(
        max_concurrent=1, slow_requeue_kbps_getter=lambda: 0)
    manager.add_item(URL, dest=DEST, state='下載中')
    task = _active_task(manager, now[0])
    restart_calls = []
    monkeypatch.setattr(
        manager, 'restart',
        lambda url, dest, automatic=False: restart_calls.append(1))

    now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
    manager._on_context_progress(task, 1, 100, SLOW_BPS)

    assert restart_calls == []
    assert task.slow_requeue_triggered is False


def test_no_restart_before_grace_elapses(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    manager = DownloadManager(
        max_concurrent=1, slow_requeue_kbps_getter=lambda: THRESHOLD_KBPS)
    manager.add_item(URL, dest=DEST, state='下載中')
    task = _active_task(manager, now[0])
    restart_calls = []
    monkeypatch.setattr(
        manager, 'restart',
        lambda url, dest, automatic=False: restart_calls.append(1))

    now[0] += SLOW_REQUEUE_GRACE_SECONDS - 1
    manager._on_context_progress(task, 1, 100, SLOW_BPS)

    assert restart_calls == []
    assert task.slow_requeue_triggered is False


def test_no_restart_when_speed_meets_threshold(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    manager = DownloadManager(
        max_concurrent=1, slow_requeue_kbps_getter=lambda: THRESHOLD_KBPS)
    manager.add_item(URL, dest=DEST, state='下載中')
    task = _active_task(manager, now[0])
    restart_calls = []
    monkeypatch.setattr(
        manager, 'restart',
        lambda url, dest, automatic=False: restart_calls.append(1))

    now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
    manager._on_context_progress(task, 1, 100, THRESHOLD_KBPS * 1024)

    assert restart_calls == []
    assert task.slow_requeue_triggered is False


def test_threshold_preference_is_read_once_per_task_after_grace(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    reads = []

    def getter():
        reads.append(1)
        return THRESHOLD_KBPS

    manager = DownloadManager(max_concurrent=1, slow_requeue_kbps_getter=getter)
    manager.add_item(URL, dest=DEST, state='下載中')
    task = _active_task(manager, now[0])

    for tick in range(5):
        manager._on_context_progress(task, tick, 100, THRESHOLD_KBPS * 2048)
    assert reads == []   # still inside the grace period

    now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
    for tick in range(5):
        manager._on_context_progress(task, tick, 100, THRESHOLD_KBPS * 2048)
    assert reads == [1]


def test_no_restart_when_global_speed_cap_active(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(sites_base.speed_limiter, '_limit_bps', 900_000)
    manager = DownloadManager(
        max_concurrent=1, slow_requeue_kbps_getter=lambda: THRESHOLD_KBPS)
    manager.add_item(URL, dest=DEST, state='下載中')
    task = _active_task(manager, now[0])
    restart_calls = []
    monkeypatch.setattr(
        manager, 'restart',
        lambda url, dest, automatic=False: restart_calls.append(1))

    now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
    manager._on_context_progress(task, 1, 100, SLOW_BPS)

    assert restart_calls == []
    assert task.slow_requeue_triggered is False


def test_exhausted_after_max_attempts_flags_item_and_keeps_downloading(
        monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gui_modern.time, 'monotonic', lambda: now[0])
    manager = DownloadManager(
        max_concurrent=1, slow_requeue_kbps_getter=lambda: THRESHOLD_KBPS)
    item = manager.add_item(URL, dest=DEST, state='下載中')
    restart_calls = []
    monkeypatch.setattr(
        manager, 'restart',
        lambda url, dest, automatic=False: restart_calls.append(automatic)
        or True)

    for _ in range(SLOW_REQUEUE_MAX_ATTEMPTS):
        now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
        task = _active_task(manager, now[0] - SLOW_REQUEUE_GRACE_SECONDS - 1)
        manager._on_context_progress(task, 1, 100, SLOW_BPS)

    assert len(restart_calls) == SLOW_REQUEUE_MAX_ATTEMPTS
    assert item.auto_requeues == SLOW_REQUEUE_MAX_ATTEMPTS
    assert item.error == ''

    # A fourth slow episode (fresh task, as a real restart would produce)
    # must not restart again, but must flag the item while it keeps
    # downloading.
    now[0] += SLOW_REQUEUE_GRACE_SECONDS + 1
    task4 = _active_task(manager, now[0] - SLOW_REQUEUE_GRACE_SECONDS - 1)
    manager._on_context_progress(task4, 1, 100, SLOW_BPS)

    assert len(restart_calls) == SLOW_REQUEUE_MAX_ATTEMPTS
    assert item.state == '下載中'
    assert item.error == T(
        'slow_requeue_exhausted', n=SLOW_REQUEUE_MAX_ATTEMPTS)


def test_manual_restart_resets_automatic_counter(monkeypatch):
    manager = DownloadManager(max_concurrent=1)
    item = manager.add_item(URL, dest=DEST, state='下載中')
    item.auto_requeues = 2
    task = _DownloadTask(URL, DEST, manager._cancel_epoch)
    with manager._lock:
        manager._active[URL] = task
    monkeypatch.setattr(manager, '_start_download_thread', lambda t: None)

    assert manager.restart(URL, DEST) is True

    assert item.auto_requeues == 0


def test_automatic_restart_does_not_reset_counter(monkeypatch):
    manager = DownloadManager(max_concurrent=1)
    item = manager.add_item(URL, dest=DEST, state='下載中')
    item.auto_requeues = 1
    task = _DownloadTask(URL, DEST, manager._cancel_epoch)
    with manager._lock:
        manager._active[URL] = task
    monkeypatch.setattr(manager, '_start_download_thread', lambda t: None)

    assert manager.restart(URL, DEST, automatic=True) is True

    assert item.auto_requeues == 1


def test_retry_download_fallback_resets_automatic_counter():
    from uav_downloader.apps.browse import DownloadItem

    item = DownloadItem(URL, state='未完成', dest=DEST)
    item.auto_requeues = 3
    item.error = 'old warning'

    class FakeManager:
        def __init__(self):
            self.calls = []

        def get_items(self):
            return [item]

        def enqueue(self, url, dest):
            self.calls.append((url, dest))

    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._dlmgr = FakeManager()
    app._dest_var = types.SimpleNamespace(get=lambda: DEST)

    app._retry_download(item.url)

    assert item.auto_requeues == 0
    assert app._dlmgr.calls == [(item.url, DEST)]


def test_slow_requeue_kbps_round_trip_clamps_and_persists(
        tmp_path, monkeypatch):
    path = tmp_path / 'ui_prefs.json'
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(path))

    assert config.get_slow_requeue_kbps() == 0
    assert config.set_slow_requeue_kbps('750') == 750
    assert config.get_slow_requeue_kbps() == 750
    assert config.set_slow_requeue_kbps(-5) == 0
    assert config.set_slow_requeue_kbps(999999) == config.MAX_SLOW_REQUEUE_KBPS

    stored = json.loads(path.read_text(encoding='utf-8'))
    assert stored['slow_requeue_kbps'] == config.MAX_SLOW_REQUEUE_KBPS

    path.write_text(
        json.dumps({'slow_requeue_kbps': 'not-a-number'}), encoding='utf-8')
    assert config.get_slow_requeue_kbps() == 0
