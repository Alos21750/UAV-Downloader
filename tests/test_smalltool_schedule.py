from datetime import datetime, timedelta, timezone
import threading
import time

from uav_downloader.core import watcher
from uav_downloader.apps import watch as jable_smalltool


def _interval_config(hours=24, last_check_iso=None):
    cfg = {
        'scan_schedule': {
            'mode': 'interval',
            'interval_hours': hours,
            'daily_time': '18:00',
        },
    }
    if last_check_iso is not None:
        cfg['last_check_iso'] = last_check_iso
    return cfg


def test_legacy_and_invalid_schedules_normalize_safely():
    assert watcher._normalize_scan_schedule(None) == {
        'mode': 'interval',
        'interval_hours': 24,
        'daily_time': '18:00',
    }
    assert watcher._normalize_scan_schedule({
        'mode': 'unknown',
        'interval_hours': 0,
        'daily_time': '25:99',
    }) == {
        'mode': 'interval',
        'interval_hours': 24,
        'daily_time': '18:00',
    }
    assert watcher._normalize_scan_schedule({
        'mode': 'daily',
        'interval_hours': '1',
        'daily_time': '00:00',
    }) == {
        'mode': 'daily',
        'interval_hours': 1,
        'daily_time': '00:00',
    }
    assert watcher._normalize_scan_schedule({
        'mode': 'daily',
        'interval_hours': 168,
        'daily_time': '23:59',
    })['daily_time'] == '23:59'


def test_interval_schedule_uses_last_success_and_supports_one_hour():
    last = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
    now = last + timedelta(minutes=30)
    local = now.astimezone(timezone(timedelta(hours=8)))
    plan = watcher._plan_next_scan(
        _interval_config(1, last.isoformat()),
        now_utc=now,
        now_local=local,
    )

    assert plan.due is False
    assert plan.delay_seconds == 30 * 60

    due = watcher._plan_next_scan(
        _interval_config(1, last.isoformat()),
        now_utc=last + timedelta(hours=1),
        now_local=(last + timedelta(hours=1)).astimezone(
            timezone(timedelta(hours=8))),
    )
    assert due.due is True
    assert due.delay_seconds == 0


def test_daily_schedule_waits_catches_up_and_runs_once_per_local_day():
    tz = timezone(timedelta(hours=8), name='UTC+08')
    before = datetime(2026, 7, 23, 16, 59, tzinfo=tz)
    cfg = {
        'scan_schedule': {
            'mode': 'daily',
            'interval_hours': 24,
            'daily_time': '17:00',
        },
    }

    waiting = watcher._plan_next_scan(
        cfg, now_utc=before.astimezone(timezone.utc), now_local=before)
    assert waiting.due is False
    assert waiting.delay_seconds == 60
    assert waiting.daily_slot == 'daily|17:00|2026-07-23'

    after = before + timedelta(minutes=2)
    catch_up = watcher._plan_next_scan(
        cfg, now_utc=after.astimezone(timezone.utc), now_local=after)
    assert catch_up.due is True
    assert catch_up.daily_slot == 'daily|17:00|2026-07-23'

    cfg['last_daily_slot'] = catch_up.daily_slot
    tomorrow = watcher._plan_next_scan(
        cfg, now_utc=after.astimezone(timezone.utc), now_local=after)
    assert tomorrow.due is False
    assert tomorrow.target_local.date().isoformat() == '2026-07-24'


def test_scan_success_patch_does_not_overwrite_new_schedule(
        monkeypatch, tmp_path):
    state_dir = tmp_path / 'state'
    monkeypatch.setattr(watcher, 'STATE_DIR', str(state_dir))
    monkeypatch.setattr(
        watcher, 'CONFIG_PATH', str(state_dir / 'config.json'))

    original = watcher.load_config()
    watcher.save_config(original)
    stale_worker_copy = watcher.load_config()

    new_schedule = {
        'mode': 'daily',
        'interval_hours': 1,
        'daily_time': '17:00',
    }
    watcher.update_config({'scan_schedule': new_schedule})

    worker = watcher.SmallToolWorker(lambda _line: None)
    completed_local = datetime(
        2026, 7, 23, 17, 5,
        tzinfo=timezone(timedelta(hours=8), name='UTC+08'))
    worker._record_scan_success(
        stale_worker_copy,
        now_utc=completed_local.astimezone(timezone.utc),
        now_local=completed_local,
    )

    saved = watcher.load_config()
    assert saved['scan_schedule'] == new_schedule
    assert saved['first_run_done'] is True
    assert saved['last_daily_slot'] == 'daily|17:00|2026-07-23'


def test_worker_is_single_flight_across_check_now_and_start(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        watcher, 'load_config', lambda: _interval_config())

    worker = watcher.SmallToolWorker(lambda _line: None)

    def blocking_scan(_cfg):
        started.set()
        release.wait(timeout=2)
        return True

    monkeypatch.setattr(worker, '_scan_and_download', blocking_scan)
    monkeypatch.setattr(worker, '_record_scan_success', lambda *_a, **_k: None)

    assert worker.start_once() is True
    assert started.wait(timeout=1)
    assert worker.start_monitoring() is False
    assert worker.request_scan_now() == 'running'

    release.set()
    assert worker.wait_until_stopped(timeout=2) is True


def test_check_now_wakes_waiting_monitor_and_requests_coalesce(monkeypatch):
    now = datetime.now(timezone.utc)
    cfg = _interval_config(168, now.isoformat())
    monkeypatch.setattr(watcher, 'load_config', lambda: dict(cfg))

    worker = watcher.SmallToolWorker(lambda _line: None)
    scans = []
    scanned = threading.Event()

    def scan(_cfg):
        scans.append(time.monotonic())
        scanned.set()
        return True

    monkeypatch.setattr(worker, '_scan_and_download', scan)
    monkeypatch.setattr(worker, '_record_scan_success', lambda *_a, **_k: None)

    assert worker.start_monitoring() is True
    time.sleep(0.05)
    assert scans == []
    assert worker.request_scan_now() == 'queued'
    assert worker.request_scan_now() == 'queued'
    assert scanned.wait(timeout=1)
    time.sleep(0.05)
    assert len(scans) == 1

    worker.stop()
    assert worker.wait_until_stopped(timeout=2) is True
    assert worker.start_monitoring() is True
    worker.stop()
    assert worker.wait_until_stopped(timeout=2) is True


def test_control_wait_observes_change_that_happened_before_wait():
    worker = watcher.SmallToolWorker(lambda _line: None)
    generation = worker.run_generation
    revision = worker._schedule_revision
    worker.notify_schedule_changed()

    started = time.monotonic()
    worker._wait_for_control(1, generation, revision)

    assert time.monotonic() - started < 0.2


def test_concurrent_config_patches_preserve_unrelated_fields(
        monkeypatch, tmp_path):
    state_dir = tmp_path / 'state'
    monkeypatch.setattr(watcher, 'STATE_DIR', str(state_dir))
    monkeypatch.setattr(
        watcher, 'CONFIG_PATH', str(state_dir / 'config.json'))
    watcher.save_config(watcher.load_config())

    barrier = threading.Barrier(3)

    def patch(value):
        barrier.wait()
        watcher.update_config(value)

    first = threading.Thread(
        target=patch, args=({'resolution': '720'},))
    second = threading.Thread(
        target=patch, args=({'subtitle_mode': 'all'},))
    first.start()
    second.start()
    barrier.wait()
    first.join(timeout=2)
    second.join(timeout=2)

    saved = watcher.load_config()
    assert saved['resolution'] == '720'
    assert saved['subtitle_mode'] == 'all'


def test_window_size_stays_inside_scaled_work_area():
    assert jable_smalltool._initial_window_size(1920, 1032) == (1180, 780)
    assert jable_smalltool._initial_window_size(1280, 688) == (1180, 648)
    assert jable_smalltool._initial_window_size(910, 485) == (886, 461)


def test_advanced_settings_viewport_is_bounded_for_small_windows():
    assert jable_smalltool._settings_viewport_height(320) == 104
    assert jable_smalltool._settings_viewport_height(440) == 150
    assert jable_smalltool._settings_viewport_height(680) == 260
    assert jable_smalltool._settings_viewport_height(900) == 260
