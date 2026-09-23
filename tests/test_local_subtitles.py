"""Issue #45: subtitle legacy local video files that predate AI subtitles."""

import csv
import sys
import threading
import time
import types

import pytest


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

from uav_downloader.apps import browse as gui_modern
from uav_downloader.apps.browse import DownloadManager, ModernApp
from uav_downloader.subtitles.engine import SubtitleResult


def _wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class _AppStandIn:
    """Duck-typed stand-in exposing only what _retry_download reads, so the
    real ModernApp (a full customtkinter window) never has to be built."""

    def __init__(self, dlmgr, dest='download'):
        self._dlmgr = dlmgr
        self._dest_var = types.SimpleNamespace(get=lambda: dest)


def test_enqueue_local_subtitles_runs_pipeline_on_existing_file(
        monkeypatch, tmp_path):
    video = tmp_path / 'legacy.mp4'
    video.write_bytes(b'video')
    calls = []

    def fake_generate(path, mode, progress_callback=None, cancel_check=None,
                      **kwargs):
        calls.append((path, mode))
        return SubtitleResult((str(tmp_path / 'legacy.en.srt'),), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', fake_generate)
    manager = DownloadManager()

    assert manager.enqueue_local_subtitles(str(video), 'en') is True

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    items = manager.get_items()
    assert len(items) == 1
    item = items[0]
    assert item.url == str(video)
    assert item.local is True
    assert item.name == 'legacy.mp4'
    assert item.dest == str(tmp_path)
    assert item.state == '已下載'
    assert item.error == ''
    assert calls == [(str(video), 'en')]


def test_enqueue_local_subtitles_rejects_none_mode_and_missing_file(tmp_path):
    manager = DownloadManager()
    video = tmp_path / 'legacy.mp4'
    video.write_bytes(b'video')

    assert manager.enqueue_local_subtitles(str(video), 'none') is False
    assert manager.get_items() == []

    missing = tmp_path / 'missing.mp4'
    assert manager.enqueue_local_subtitles(str(missing), 'en') is False
    assert manager.get_items() == []


def test_enqueue_local_subtitles_dedupes_inflight_path(monkeypatch, tmp_path):
    video = tmp_path / 'legacy.mp4'
    video.write_bytes(b'video')
    started = threading.Event()
    release = threading.Event()

    def slow_generate(path, mode, progress_callback=None, cancel_check=None,
                      **kwargs):
        started.set()
        assert release.wait(3)
        return SubtitleResult((), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', slow_generate)
    manager = DownloadManager()

    assert manager.enqueue_local_subtitles(str(video), 'en') is True
    assert started.wait(2)
    assert manager.enqueue_local_subtitles(str(video), 'en') is False
    assert manager.subtitle_pending_count == 0
    assert manager.subtitle_active_count == 1

    release.set()
    assert _wait_until(lambda: manager.subtitle_active_count == 0)


def test_retry_local_item_reruns_subtitles_never_downloads(
        monkeypatch, tmp_path):
    video = tmp_path / 'legacy.mp4'
    video.write_bytes(b'video')
    calls = []

    def failing_then_ok(path, mode, progress_callback=None,
                        cancel_check=None, **kwargs):
        calls.append((path, mode))
        if len(calls) == 1:
            raise RuntimeError('offline')
        return SubtitleResult((str(tmp_path / 'legacy.en.srt'),), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', failing_then_ok)
    create_site_calls = []
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda *a, **k: create_site_calls.append((a, k)))
    monkeypatch.setattr(gui_modern.config, 'get_subtitle_pref', lambda: 'en')

    manager = DownloadManager()
    assert manager.enqueue_local_subtitles(str(video), 'en') is True
    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    item = manager.get_items()[0]
    assert item.state == '已下載'
    assert 'offline' in item.error

    enqueue_calls = []
    monkeypatch.setattr(
        manager, 'enqueue', lambda *a, **k: enqueue_calls.append((a, k)))

    app = _AppStandIn(manager)
    ModernApp._retry_download(app, str(video))

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    item = manager.get_items()[0]
    assert item.state == '已下載'
    assert item.error == ''
    assert calls == [(str(video), 'en'), (str(video), 'en')]
    assert enqueue_calls == []
    assert create_site_calls == []


def test_save_csv_excludes_local_items(monkeypatch, tmp_path):
    video = tmp_path / 'legacy.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        gui_modern, 'generate_subtitles',
        lambda *a, **k: SubtitleResult((), ()))
    manager = DownloadManager()
    manager.add_item('https://jable.tv/videos/sample/', state='未完成')

    assert manager.enqueue_local_subtitles(str(video), 'en') is True
    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)

    csv_path = tmp_path / 'queue.csv'
    manager.save_csv(str(csv_path))

    with open(csv_path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    urls = [row['網址'] for row in rows]
    assert str(video) not in urls
    assert 'https://jable.tv/videos/sample/' in urls


def test_cancel_all_cancels_local_subtitle_item(monkeypatch, tmp_path):
    video = tmp_path / 'legacy.mp4'
    video.write_bytes(b'video')
    started = threading.Event()
    release = threading.Event()

    def slow_generate(path, mode, progress_callback=None, cancel_check=None,
                      **kwargs):
        started.set()
        assert release.wait(3)
        return SubtitleResult((), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', slow_generate)
    manager = DownloadManager()

    assert manager.enqueue_local_subtitles(str(video), 'en') is True
    assert started.wait(2)

    manager.cancel_all()
    assert manager.get_items()[0].state == '已取消'

    release.set()
    assert _wait_until(lambda: manager.subtitle_active_count == 0)
    assert manager.get_items()[0].state == '已取消'
