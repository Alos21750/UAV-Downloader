from concurrent.futures import Future
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication
from uav_downloader.ui.qt.settings_controller import SettingsController


def test_pending_save_never_blocks_gui_or_reports_success_early():
    app = QCoreApplication.instance() or QCoreApplication([])
    calls = []
    def submit(action, *args):
        future = Future()
        calls.append((action, args, future))
        return future
    controller = SettingsController(SimpleNamespace(submit=submit))
    controller.timer.stop()
    saved, errors = [], []
    controller.saved.connect(lambda: saved.append(True))
    controller.errorOccurred.connect(errors.append)
    try:
        calls[-1][2].set_result(dict(resolution='highest'))
        controller.poll()
        assert calls[-1][0] == 'get_translation'
        calls[-1][2].set_result(dict(provider='local', key_source='none'))
        controller.poll()
        assert calls[-1][0] == 'get_cf'
        calls[-1][2].set_result(dict(host='jable.tv', has_cookie=False))
        controller.poll()
        controller.save(dict(resolution='720'))
        assert controller.busy and not saved
        controller.poll()
        assert controller.busy
        calls[-1][2].set_exception(OSError('disk full'))
        controller.poll()
        assert not controller.busy and not saved and errors == ['disk full']
        assert controller.values == dict(resolution='highest')
        controller.save(dict(resolution='720'))
        calls[-1][2].set_result(dict(resolution='720'))
        controller.poll()
        assert saved == [True] and controller.values['resolution'] == '720'
    finally:
        controller.abandon()


def test_failed_save_during_close_requires_a_decision():
    from uav_downloader.ui.qt.task_controller import TaskController
    import queue
    app = QCoreApplication.instance() or QCoreApplication([])
    pending = Future()
    settings = SettingsController(SimpleNamespace(submit=lambda *args: pending))
    settings.timer.stop()
    settings._pending = ('set_preferences', pending)
    ready, closing = Future(), Future()
    ready.set_result(True)
    closing.set_result(True)
    service = SimpleNamespace(ready=ready, errors=queue.SimpleQueue(),
        manager=SimpleNamespace(snapshot_items=lambda: ()), close=lambda: closing, abandon=lambda: None)
    tasks = TaskController('', service=service)
    tasks.timer.stop()
    tasks.addCloseService(settings)
    errors, exits = [], []
    tasks.closeNeedsDecision.connect(errors.append)
    tasks.closeReady.connect(lambda: exits.append(True))
    try:
        tasks.requestClose()
        tasks.poll()
        assert not exits
        pending.set_exception(OSError('Settings disk full'))
        settings.poll()
        tasks.poll()
        assert errors == ['Settings disk full'] and not exits
        # The error has been presented; an explicit retry can complete shutdown.
        tasks.requestClose()
        tasks.poll()
        assert exits == [True]
    finally:
        settings.abandon()


def test_retry_after_simultaneous_settings_and_monitor_write_failure_persists_both(tmp_path, monkeypatch):
    from uav_downloader.core import config
    from uav_downloader.core.task_service import TaskService
    from uav_downloader.core.watcher_service import WatcherService
    from uav_downloader.ui.qt.task_controller import TaskController
    from uav_downloader.ui.qt.watcher_controller import WatcherController
    app = QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(tmp_path / 'preferences.json'))
    service = TaskService(str(tmp_path / 'queue.csv'))
    monitor_service = WatcherService(tmp_path / 'watcher')
    settings = SettingsController(service)
    watcher = WatcherController('', service=monitor_service)
    tasks = TaskController('', service=service)
    for adapter in (settings, watcher, tasks):
        adapter.timer.stop()
    tasks.addCloseService(settings)
    tasks.addCloseService(watcher)
    exits, decisions = [], []
    tasks.closeReady.connect(lambda: exits.append(True))
    tasks.closeNeedsDecision.connect(decisions.append)
    try:
        service.ready.result(3)
        monitor_service.ready.result(3)
        while settings.busy:
            settings._pending[1].result(3)
            settings.poll()
        watcher.poll()
        tasks.poll()
        original_pref, original_monitor = config._save_prefs, monitor_service.store._write
        calls = dict(preferences=0, monitor=0)
        def save_prefs(value):
            calls['preferences'] += 1
            if calls['preferences'] == 1:
                raise OSError('Preferences disk full')
            return original_pref(value)
        def save_monitor(*args):
            calls['monitor'] += 1
            if calls['monitor'] == 1:
                raise OSError('Monitor disk full')
            return original_monitor(*args)
        monkeypatch.setattr(config, '_save_prefs', save_prefs)
        monkeypatch.setattr(monitor_service.store, '_write', save_monitor)
        settings.save(dict(resolution='720'))
        watcher.apply(dict(baseline_date='2026-01-01'), 'save')
        tasks.requestClose()
        for future in [tasks._close_future, *tasks._other_close_futures]:
            with pytest.raises(OSError):
                future.result(3)
        # Retry even before the individual GUI adapters have polled their old
        # failed commands, which can happen between two timer ticks.
        tasks.poll()
        assert not exits and len(decisions) == 1
        tasks.requestClose()
        for future in [tasks._close_future, *tasks._other_close_futures]:
            assert future.result(3)
        tasks.poll()
        assert exits == [True] and calls == dict(preferences=2, monitor=2)
        assert config.get_resolution_pref() == '720'
        assert monitor_service.store.load_config()['baseline_date'] == '2026-01-01'
    finally:
        settings.abandon()
        watcher.abandon()
        service.abandon()
