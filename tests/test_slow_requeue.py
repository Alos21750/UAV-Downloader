"""Slow-transfer monitoring, identity guards, and watchdog lifecycle."""
import json
import threading
import time
import types

import pytest

from uav_downloader.apps import browse
from uav_downloader.apps.browse import DownloadManager, _DownloadTask
from uav_downloader.core import config
from uav_downloader.i18n.locales import T
from uav_downloader.sites import base

URL = 'https://supjav.com/slow-requeue.html'
DEST = 'download'
KB = 1024


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(browse.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(base.speed_limiter, '_limit_bps', 0)
    return now


def active(clock, threshold=750, state='下載中'):
    manager = DownloadManager(slow_requeue_kbps_getter=lambda: threshold)
    item = manager.add_item(URL, dest=DEST, state=state)
    task = _DownloadTask(URL, DEST, manager._cancel_epoch)
    task.job = types.SimpleNamespace(_bytes_downloaded=0)
    task.download_started = task.window_started = clock[0]
    manager._active[URL] = task
    return manager, item, task


def check(manager, task, now):
    with manager._lock:
        return manager._check_slow_requeue_locked(task, now)


def advance(clock, task, seconds=60, speed=0):
    clock[0] += seconds
    task.transferred_bytes += speed * seconds
    task.job._bytes_downloaded = task.transferred_bytes


def test_completed_progress_never_requests_automatic_restart(clock, monkeypatch):
    manager, item, task = active(clock)
    calls = []
    monkeypatch.setattr(manager, 'restart', lambda *a, **k: calls.append((a, k)))
    clock[0] += 61
    task.job._bytes_downloaded = 100 * KB * 61
    manager._on_context_progress(task, 2, 2, 100 * KB)
    assert not calls
    assert item.auto_requeues == 0
    assert task.transfer_complete
    assert not check(manager, task, clock[0] + 600)


def test_completion_after_decision_is_rechecked_before_cancel(clock):
    manager, item, task = active(clock)
    advance(clock, task)
    assert check(manager, task, clock[0])
    manager._on_context_progress(task, 100, 100, 0)
    assert not manager.restart(URL, DEST, automatic=True, expected_task=task)
    assert not task.cancelled.is_set()
    assert item.auto_requeues == 0


def test_delayed_automatic_restart_cannot_cancel_replacement(clock, monkeypatch):
    manager, item, old = active(clock)
    monkeypatch.setattr(manager, '_start_download_thread', lambda task: None)
    assert manager.restart(URL, DEST)
    manager._complete_download(old, '已取消')
    replacement = manager._active[URL]
    assert not manager.restart(URL, DEST, automatic=True, expected_task=old)
    assert not replacement.cancelled.is_set()
    assert item.auto_requeues == 0


@pytest.mark.parametrize('guard', ['cancel', 'epoch', 'complete', 'stop', 'storage'])
def test_automatic_restart_rechecks_task_guards(clock, guard):
    manager, item, task = active(clock)
    if guard == 'cancel':
        task.cancelled.set()
    elif guard == 'epoch':
        manager._cancel_epoch += 1
    elif guard == 'complete':
        task.transfer_complete = True
    elif guard == 'stop':
        task.monitor_stop.set()
    else:
        task.job._storage_error = RuntimeError('disk disconnected')
    assert not manager.restart(URL, DEST, automatic=True, expected_task=task)
    assert item.auto_requeues == 0


@pytest.mark.parametrize('state', ['準備中', '字幕準備中', '字幕辨識中', '已下載'])
def test_monitor_never_requeues_preparation_subtitle_or_finished_rows(clock, state):
    manager, item, task = active(clock, state=state)
    advance(clock, task)
    assert not check(manager, task, clock[0])
    assert not manager.restart(URL, DEST, automatic=True, expected_task=task)
    assert item.auto_requeues == 0


def test_slow_initial_window_requests_only_one_restart(clock):
    manager, item, task = active(clock)
    advance(clock, task, seconds=59, speed=100 * KB)
    assert not check(manager, task, clock[0])
    advance(clock, task, seconds=1, speed=100 * KB)
    assert check(manager, task, clock[0])
    assert not check(manager, task, clock[0] + 120)
    assert item.auto_requeues == 0
    assert manager.restart(URL, DEST, automatic=True, expected_task=task)
    assert item.auto_requeues == 1


def test_fast_then_slow_uses_recent_bytes_not_lifetime_average(clock):
    manager, item, task = active(clock)
    advance(clock, task, speed=10 * 1024 * KB)
    assert not check(manager, task, clock[0])
    advance(clock, task, speed=100 * KB)
    assert task.transferred_bytes / 120 > 750 * KB
    assert check(manager, task, clock[0])


def test_fully_stalled_download_is_detected_without_any_progress_callback(clock):
    manager, item, task = active(clock)
    clock[0] += 60
    assert task.prev_total is None
    assert check(manager, task, clock[0])


def test_stall_after_fast_window_is_detected(clock):
    manager, item, task = active(clock)
    advance(clock, task, speed=4 * 1024 * KB)
    assert not check(manager, task, clock[0])
    advance(clock, task)
    assert check(manager, task, clock[0])


def test_default_off_and_enabling_later_take_effect_at_next_window(clock):
    threshold = [0]
    manager, item, task = active(clock)
    manager._slow_requeue_kbps_getter = lambda: threshold[0]
    advance(clock, task)
    assert not check(manager, task, clock[0])
    threshold[0] = 750
    advance(clock, task)
    assert check(manager, task, clock[0])


def test_speed_limit_suppresses_monitor_and_resets_window(clock, monkeypatch):
    manager, item, task = active(clock)
    monkeypatch.setattr(base.speed_limiter, '_limit_bps', 100 * KB)
    advance(clock, task)
    assert not check(manager, task, clock[0])
    monkeypatch.setattr(base.speed_limiter, '_limit_bps', 0)
    advance(clock, task, speed=1000 * KB)
    assert not check(manager, task, clock[0])


@pytest.mark.parametrize('change', ['disabled', 'cap'])
def test_option_changes_after_decision_prevent_delayed_restart(clock, monkeypatch, change):
    manager, item, task = active(clock)
    advance(clock, task)
    assert check(manager, task, clock[0])
    if change == 'disabled':
        manager._slow_requeue_kbps_getter = lambda: 0
    else:
        monkeypatch.setattr(base.speed_limiter, '_limit_bps', 100 * KB)
    assert not manager._restart_slow_download(task)
    assert not task.cancelled.is_set()
    assert item.auto_requeues == 0
    assert not task.slow_requeue_checked
    monkeypatch.setattr(base.speed_limiter, '_limit_bps', 0)
    manager._slow_requeue_kbps_getter = lambda: 750
    advance(clock, task)
    assert check(manager, task, clock[0])


def test_exact_threshold_is_not_slow(clock):
    manager, item, task = active(clock)
    advance(clock, task, speed=750 * KB)
    assert not check(manager, task, clock[0])


def test_hls_callback_samples_exact_bytes(clock, monkeypatch):
    manager, item, task = active(clock)
    calls = []
    monkeypatch.setattr(manager, 'restart', lambda *a, **k: calls.append(k))
    advance(clock, task, speed=1000 * KB)
    manager._on_context_progress(task, 10, 100, 1)
    assert not calls
    assert task.window_bytes == 1000 * KB * 60
    advance(clock, task, speed=100 * KB)
    manager._on_context_progress(task, 20, 100, 1000 * KB)
    assert _poll(lambda: len(calls) == 1)
    assert calls == [{'automatic': True, 'expected_task': task}]


def test_supjav_hls_with_st_fallback_uses_exact_hls_bytes(clock, monkeypatch):
    manager, item, task = active(clock)
    task.job._direct_url = 'https://streamtape.example/fallback.mp4'
    task.job._progress_unit = 'segments'
    task.monitor_unit = 'segments'
    calls = []
    monkeypatch.setattr(manager, 'restart', lambda *a, **k: calls.append(k))
    advance(clock, task, speed=1000 * KB)
    manager._on_context_progress(task, 10, 100, 1)
    assert not calls
    assert task.transferred_bytes == 1000 * KB * 60


def test_direct_callback_samples_bytes_and_source_change_resets_window(clock):
    manager, item, task = active(clock)
    manager._on_context_progress(task, 1, 100, 0)
    advance(clock, task, seconds=59, speed=1000 * KB)
    task.job._direct_url = 'https://cdn.example/video.mp4'
    task.job._progress_unit = 'bytes'
    manager._on_context_progress(task, 500_000, 100_000_000, 100 * KB)
    assert task.window_started == clock[0]
    assert task.transferred_bytes == 500_000
    assert not task.slow_requeue_checked
    assert not check(manager, task, clock[0] + 59)
    clock[0] += 60
    task.transferred_bytes += 100 * KB * 60
    assert check(manager, task, clock[0])


def test_unknown_length_direct_progress_is_counted_without_completion(clock):
    manager, item, task = active(clock)
    task.job._progress_unit = 'bytes'
    task.monitor_unit = 'bytes'
    clock[0] += 60
    manager._on_context_progress(task, 1000 * KB * 60, 0, 1000 * KB)
    assert not task.transfer_complete
    assert task.transferred_bytes == 1000 * KB * 60
    assert not task.slow_requeue_checked


@pytest.mark.parametrize('phase', ['preparing', 'postprocess'])
def test_explicit_job_phase_blocks_watchdog_and_delayed_cancel(clock, phase):
    manager, item, task = active(clock)
    advance(clock, task)
    assert check(manager, task, clock[0])
    task.job._progress_unit = phase
    assert not manager.restart(URL, DEST, automatic=True, expected_task=task)
    task.slow_requeue_checked = False
    assert not check(manager, task, clock[0] + 600)
    assert item.auto_requeues == 0


def test_grace_starts_after_explicit_preparation_phase(clock):
    manager, item, task = active(clock)
    task.job._progress_unit = 'preparing'
    clock[0] += 600
    assert not check(manager, task, clock[0])
    task.job._progress_unit = 'segments'
    assert not check(manager, task, clock[0])
    assert not check(manager, task, clock[0] + 59)
    assert check(manager, task, clock[0] + 60)


def test_three_requeues_then_flag_and_continue(clock, monkeypatch):
    manager, item, task = active(clock)
    monkeypatch.setattr(manager, '_start_download_thread', lambda task: None)
    for expected in range(1, 4):
        task.window_started = task.download_started = clock[0]
        item.state = '下載中'
        advance(clock, task)
        assert check(manager, task, clock[0])
        assert manager.restart(URL, DEST, automatic=True, expected_task=task)
        assert item.auto_requeues == expected
        manager._complete_download(task, '已取消')
        task = manager._active[URL]
        task.job = types.SimpleNamespace(_bytes_downloaded=0)
    task.window_started = task.download_started = clock[0]
    item.state = '下載中'
    advance(clock, task)
    assert not check(manager, task, clock[0])
    assert item.auto_requeues == 3
    assert item.state == '下載中'
    assert item.error == T('slow_requeue_exhausted', n=3)
    assert not task.cancelled.is_set()


def test_manual_restart_and_new_enqueue_reset_counter(clock, monkeypatch):
    manager, item, task = active(clock)
    item.auto_requeues = 3
    monkeypatch.setattr(manager, '_start_download_thread', lambda task: None)
    assert manager.restart(URL, DEST)
    assert item.auto_requeues == 0
    manager._complete_download(task, '已取消')
    manager._active.clear()
    item.auto_requeues = 3
    manager.enqueue(URL, DEST)
    assert item.auto_requeues == 0


def test_cancellation_stops_watchdog_and_prevents_automatic_restart(clock):
    manager, item, task = active(clock)
    manager._start_slow_monitor(task)
    manager.cancel_all(cleanup=False)
    task.monitor_thread.join(1)
    assert not task.monitor_thread.is_alive()
    assert task.monitor_stop.is_set()
    clock[0] += 1000
    assert not check(manager, task, clock[0])
    assert item.auto_requeues == 0


def test_normal_completion_stops_watchdog(clock):
    manager, item, task = active(clock)
    manager._start_slow_monitor(task)
    manager._complete_download(task, '已下載')
    task.monitor_thread.join(1)
    assert not task.monitor_thread.is_alive()


def test_subtitle_handoff_stops_download_watchdog(clock, monkeypatch):
    manager, item, task = active(clock)
    monkeypatch.setattr(manager, '_try_next_subtitle', lambda: None)
    manager._start_slow_monitor(task)
    assert manager._queue_subtitle(task, 'en')
    task.monitor_thread.join(1)
    assert not task.monitor_thread.is_alive()
    assert manager._subtitle_pending == [task]


def test_threshold_clamps_and_roundtrips(tmp_path, monkeypatch):
    path = tmp_path / 'prefs.json'
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(path))
    assert config.get_slow_requeue_kbps() == 0
    assert config.set_slow_requeue_kbps('750') == 750
    assert config.get_slow_requeue_kbps() == 750
    assert config.set_slow_requeue_kbps(-1) == 0
    assert config.set_slow_requeue_kbps(999999) == 100000
    path.write_text(json.dumps({'slow_requeue_kbps': 'bad'}), encoding='utf-8')
    assert config.get_slow_requeue_kbps() == 0


def _poll(predicate):
    for _ in range(300):
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_stalled_transfer_requeues_end_to_end_and_leaves_no_monitor(clock, monkeypatch):
    created = []
    first_started = threading.Event()
    first_task = []

    class Job:
        def __init__(self):
            self._cancel_job = False
            self._bytes_downloaded = 0
            self._progress_callback = None
            self.number = len(created)
            created.append(self)

        def is_url_vaildate(self):
            return True

        def target_name(self):
            return 'test'

        def cancel_download(self, cleanup=True):
            self._cancel_job = True

        def start_download(self):
            if self.number == 0:
                first_task.append(manager._active[URL])
                first_started.set()
                clock[0] += 61
                assert _poll(lambda: self._cancel_job)
                return False
            self._progress_callback(1, 1, 0)
            return True

    monkeypatch.setattr(browse.M3U8Sites, 'CreateSite', lambda *args: Job())
    manager = DownloadManager(subtitle_mode_getter=lambda: 'none',
                              slow_requeue_kbps_getter=lambda: 750)
    item = manager.add_item(URL, dest=DEST)
    manager.enqueue(URL, DEST)
    assert first_started.wait(1)
    assert _poll(lambda: item.state == '已下載')
    assert len(created) == 2
    assert item.auto_requeues == 1
    first_task[0].monitor_thread.join(1)
    assert not first_task[0].monitor_thread.is_alive()
    assert not manager._active
