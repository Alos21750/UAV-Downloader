import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime, timezone

import pytest

from uav_downloader.core import watcher
from uav_downloader.core.watcher_service import WatcherService
from uav_downloader.core.watcher_store import WatcherStore


def test_watcher_core_import_does_not_load_gui_frameworks(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'src'
    environment = dict(os.environ, PYTHONPATH=str(source), APPDATA=str(tmp_path))
    result = subprocess.run([sys.executable, '-c',
        "import sys; from uav_downloader.core import watcher; "
        "assert not any(name in sys.modules for name in ('tkinter', 'customtkinter', 'PySide6'))"],
        env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_independent_stores_merge_runtime_without_overwriting_preferences(tmp_path):
    first, second = WatcherStore(tmp_path / 'one'), WatcherStore(tmp_path / 'two')
    first.update_config(dict(baseline_date='2026-01-01', output_folder='first'))
    second.update_config(dict(baseline_date='2026-02-01', output_folder='second'))
    first.update_config(dict(first_run_done=True, last_check_iso='2026-10-13T00:00:00+00:00'))
    assert first.load_config()['output_folder'] == 'first'
    assert first.load_config()['baseline_date'] == '2026-01-01'
    assert second.load_config()['output_folder'] == 'second'
    assert 'last_check_iso' not in second.load_config()


def test_invalid_watcher_state_is_reported_without_overwriting_original(tmp_path):
    store = WatcherStore(tmp_path)
    for filename, content in [('config.json', '[]'), ('seen.json', '{broken-json')]:
        path = tmp_path / filename
        path.write_text(content, encoding='utf-8')
        with pytest.raises(ValueError):
            store.load_config() if filename == 'config.json' else store.load_seen()
        assert path.read_text(encoding='utf-8') == content


def test_close_waits_for_real_scan_exit_and_refuses_new_commands(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def factory(*args, **kwargs):
        worker = watcher.SmallToolWorker(*args, **kwargs)
        def scan(cfg):
            entered.set()
            assert release.wait(3)
            return True
        worker._scan_and_download = scan
        return worker
    service = WatcherService(tmp_path, factory)
    try:
        service.ready.result(3)
        service.submit('check', dict(output_folder=str(tmp_path), baseline_date='2026-01-01',
            selected_targets=[dict(site='JableTV', id='feed:latest')])).result(3)
        assert entered.wait(2)
        closing = service.close()
        assert not closing.done()
        with pytest.raises(RuntimeError, match='closing'):
            service.submit('check')
        release.set()
        assert closing.result(3) and not service.worker.is_running()
    finally:
        release.set()
        service.abandon()


def test_invalid_configuration_does_not_replace_saved_state(tmp_path):
    service = WatcherService(tmp_path)
    try:
        service.ready.result(3)
        saved = service.submit('save', dict(baseline_date='2026-01-01')).result(3)
        with pytest.raises(ValueError, match='Baseline'):
            service.submit('save', dict(baseline_date='2026-99-99')).result(3)
        assert service.store.load_config()['baseline_date'] == saved['baseline_date']
        with pytest.raises(ValueError, match='category'):
            service.submit('save', dict(selected_targets=[dict(site='JableTV', id='unknown')])).result(3)
        service.submit('save', dict(baseline_date='2026-01-01')).result(3)
        service.close().result(3)
    finally:
        service.abandon()


def test_close_retry_really_saves_failed_watcher_settings(tmp_path, monkeypatch):
    service = WatcherService(tmp_path)
    try:
        service.ready.result(3)
        original = service.store._write
        attempts = []
        def fail_once(*args):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('Watcher disk temporarily unavailable')
            return original(*args)
        monkeypatch.setattr(service.store, '_write', fail_once)
        with pytest.raises(OSError):
            service.submit('save', dict(baseline_date='2026-01-01')).result(3)
        with pytest.raises(OSError):
            service.close().result(3)
        assert not (tmp_path / 'config.json').exists()
        assert service.close().result(3)
        assert service.store.load_config()['baseline_date'] == '2026-01-01'
        assert len(attempts) == 2
    finally:
        service.abandon()


def test_standalone_watcher_preserves_its_saved_resolution_without_rewriting_browser_preferences(tmp_path, monkeypatch):
    from uav_downloader.sites import base
    from uav_downloader.core import config
    previous = base.get_resolution_pref()
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(tmp_path / 'browser-prefs.json'))
    config._save_prefs(dict(resolution='1080'))
    WatcherStore(tmp_path / 'watcher').update_config(dict(resolution='lowest'))
    service = WatcherService(tmp_path / 'watcher', apply_resolution=True)
    try:
        assert service.ready.result(3)['resolution'] == 'lowest'
        assert base.get_resolution_pref() == 'lowest' and config.get_resolution_pref() == '1080'
        service.submit('save', dict(resolution='720')).result(3)
        assert base.get_resolution_pref() == '720' and config.get_resolution_pref() == '1080'
        service.close().result(3)
    finally:
        service.abandon()
        base.set_resolution_pref(previous)
