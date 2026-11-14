import builtins
from pathlib import Path

from uav_downloader.core import config


def test_clipboard_change_only_fills_input_without_adding_to_queue():
    from uav_downloader.apps.qt_tasks import fill_clipboard_input
    values = {}
    from types import SimpleNamespace
    input_field = SimpleNamespace(setProperty=lambda name, value: values.update({name: value}))
    fill_clipboard_input(input_field, 'Copied https://jable.tv/videos/first/ https://jable.tv/videos/second/')
    assert values == {'text': 'https://jable.tv/videos/first/'}
    fill_clipboard_input(input_field, 'no URL')
    assert values == {'text': 'https://jable.tv/videos/first/'}


def test_browser_nogui_never_imports_a_gui_framework(monkeypatch):
    from uav_downloader.entrypoints import browse
    from uav_downloader import sites
    calls = []
    monkeypatch.setattr(sites, 'consoles_main', lambda *args: calls.append(args))
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert not name.startswith(('PySide6', 'tkinter', 'customtkinter', 'uav_downloader.apps'))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    assert browse.main(['--nogui', '--url', 'https://example.test/video', '-o', 'output', '--max-workers-per-video', '3']) == 0
    assert calls == [('https://example.test/video', 'output', 3)]


def test_formal_entrypoints_keep_browser_and_watcher_queues_separate(monkeypatch, tmp_path):
    from uav_downloader.entrypoints import browse, watch
    from uav_downloader.apps import qt_tasks
    calls = []
    monkeypatch.setattr(qt_tasks, 'main', lambda options: calls.append(options) or 0)
    monkeypatch.setattr(config, 'queue_csv_path', lambda: str(tmp_path / 'browser.csv'))
    assert browse.main(['--url', 'https://jable.tv/videos/test-001/', '-o', 'chosen']) == 0
    assert watch.main(['--watch-state', str(tmp_path / 'watcher')]) == 0
    browser, watcher = calls
    assert browser[browser.index('--workspace') + 1] == 'browser'
    assert browser[browser.index('--queue') + 1] == str(tmp_path / 'browser.csv')
    assert browser[browser.index('--initial-url') + 1] == 'https://jable.tv/videos/test-001/'
    assert watcher[watcher.index('--workspace') + 1] == 'watcher'
    assert Path(watcher[watcher.index('--queue') + 1]) == tmp_path / 'watcher/qt-settings-queue.csv'
