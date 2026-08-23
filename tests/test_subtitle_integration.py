import ast
import builtins
import os
import runpy
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from uav_downloader.apps import browse as gui_modern
from uav_downloader.apps import watch as jable_smalltool
from uav_downloader import core, subtitles
from uav_downloader.core import bootstrap
from uav_downloader.i18n import locales
from uav_downloader.subtitles.engine import SubtitleResult


def _wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_smalltool_defers_subtitle_engine_until_download():
    source = Path(jable_smalltool.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    eager_imports = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            getattr(node, 'module', None) in {
                'subtitle_engine',
                'uav_downloader.subtitles.engine',
            }
            or any(alias.name in {
                       'subtitle_engine',
                       'uav_downloader.subtitles.engine',
                   }
                   for alias in getattr(node, 'names', ()))
        )
    ]
    assert eager_imports == []


def test_both_frozen_entry_points_expose_explicit_translation_diagnostic():
    entrypoint_root = (
        Path(__file__).resolve().parents[1]
        / 'src' / 'uav_downloader' / 'entrypoints'
    )
    bootstrap_source = Path(bootstrap.__file__).read_text(encoding='utf-8')
    for prefix in ('UAV', 'ALOS', 'JABLE'):
        assert f'{prefix}_LOCAL_TRANSLATION_DIAGNOSTIC_OUTPUT' in (
            bootstrap_source)
        assert f'{prefix}_LOCAL_TRANSLATION_SOAK_DIAGNOSTIC_OUTPUT' in (
            bootstrap_source)
        assert f'{prefix}_LLM_TRANSLATION_DIAGNOSTIC_OUTPUT' in (
            bootstrap_source)
    for filename in ('browse.py', 'watch.py'):
        source = (entrypoint_root / filename).read_text(encoding='utf-8')
        assert 'run_translation_diagnostic_if_requested' in source
    assert 'run_local_translation_diagnostic' in bootstrap_source
    assert 'run_local_translation_worker_soak_diagnostic' in bootstrap_source
    assert 'run_llm_translation_diagnostic' in bootstrap_source


def test_llm_diagnostic_exits_both_entry_points_before_gui_import(
        monkeypatch, tmp_path):
    root = (
        Path(__file__).resolve().parents[1]
        / 'src' / 'uav_downloader' / 'entrypoints'
    )
    calls = []
    fake_engine = types.ModuleType('uav_downloader.subtitles.engine')

    def fake_run(output):
        calls.append(output)
        Path(output).write_text(
            '{"kind":"llm-diagnostic","schema":1}',
            encoding='utf-8')

    fake_engine.run_llm_translation_diagnostic = fake_run
    fake_engine.run_local_translation_diagnostic = lambda _output: None
    fake_engine.run_local_translation_worker_soak_diagnostic = (
        lambda _output: None)
    fake_crashlog = types.ModuleType('uav_downloader.core.crashlog')
    fake_crashlog.install = lambda: None
    monkeypatch.setitem(
        sys.modules, 'uav_downloader.subtitles.engine', fake_engine)
    monkeypatch.setitem(
        sys.modules, 'uav_downloader.core.crashlog', fake_crashlog)
    monkeypatch.setattr(subtitles, 'engine', fake_engine, raising=False)
    monkeypatch.setattr(core, 'crashlog', fake_crashlog, raising=False)
    monkeypatch.delenv(
        'JABLE_LOCAL_TRANSLATION_DIAGNOSTIC_OUTPUT', raising=False)
    monkeypatch.delenv(
        'JABLE_LOCAL_TRANSLATION_SOAK_DIAGNOSTIC_OUTPUT',
        raising=False)

    real_import = builtins.__import__
    gui_prefixes = {
        'uav_downloader.apps',
        'uav_downloader.i18n',
        'uav_downloader.legacy',
        'uav_downloader.sites',
        'uav_downloader.ui',
        'customtkinter',
        'tkinter',
    }

    def reject_gui_import(name, *args, **kwargs):
        module_name = str(name)
        if any(
            module_name == prefix or module_name.startswith(prefix + '.')
            for prefix in gui_prefixes
        ):
            raise AssertionError(
                f'GUI/heavy module imported during diagnostic: {name}')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', reject_gui_import)
    for filename in ('browse.py', 'watch.py'):
        output = str(tmp_path / f'{filename}.json')
        monkeypatch.setenv(
            'JABLE_LLM_TRANSLATION_DIAGNOSTIC_OUTPUT', output)
        with pytest.raises(SystemExit) as caught:
            runpy.run_path(str(root / filename), run_name='__main__')
        assert caught.value.code == 0
        assert calls[-1] == output


class FakeDownloadJob:
    def __init__(self, video_path):
        self.video_path = str(video_path)
        self._cancel_job = False
        self._progress_callback = None

    def is_url_vaildate(self):
        return True

    def target_name(self):
        return 'sample'

    def start_download(self):
        if self._progress_callback:
            self._progress_callback(10, 10, 1000)
        return True

    def _get_video_savename(self):
        return self.video_path

    def cancel_download(self, cleanup=True):
        self._cancel_job = True


def test_watcher_preserves_valid_segments_for_incomplete_retry(
        monkeypatch, tmp_path):
    temp_folder = tmp_path / 'missav-sample'
    temp_folder.mkdir()
    completed_segment = temp_folder / '00001.ts'
    completed_segment.write_bytes(b'validated segment')

    class IncompleteDownloadJob(FakeDownloadJob):
        def __init__(self):
            super().__init__(tmp_path / 'sample.mp4')
            self._temp_folder = str(temp_folder)

        def start_download(self):
            raise jable_smalltool.DownloadIncompleteError(1)

    job = IncompleteDownloadJob()
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(
        jable_smalltool.M3U8Sites, 'CreateSite', lambda _url, _dest: job)
    logs = []
    worker = jable_smalltool.SmallToolWorker(logs.append)
    marked = []
    worker._mark_seen = lambda *args, **kwargs: marked.append(args)

    result = worker._download_one({
        'url': 'https://missav.ws/sample-001',
        'title': 'SAMPLE-001 title',
        '_site': 'MissAV',
    }, str(tmp_path))

    assert result == 'download_incomplete'
    assert completed_segment.read_bytes() == b'validated segment'
    assert marked == []
    assert any('[RETRY]' in line and '1 segment' in line for line in logs)


def test_modern_manager_runs_selected_subtitle_mode(monkeypatch, tmp_path):
    video = tmp_path / 'sample.mp4'
    video.write_bytes(b'video')
    job = FakeDownloadJob(video)
    monkeypatch.setattr(gui_modern.M3U8Sites, 'CreateSite', lambda _url, _dest: job)
    calls = []

    def fake_generate(path, mode, progress_callback=None, cancel_check=None):
        calls.append((path, mode))
        progress_callback('transcribe_ja', None)
        return SubtitleResult((str(tmp_path / 'sample.ja.srt'),), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', fake_generate)
    manager = gui_modern.DownloadManager(subtitle_mode_getter=lambda: 'ja')
    manager.add_item('https://jable.tv/videos/sample/', state='等待中')

    manager._run('https://jable.tv/videos/sample/', str(tmp_path), epoch=0)

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0
        and manager.get_items()[0].state == '已下載')
    item = manager.get_items()[0]
    assert calls == [(str(video), 'ja')]
    assert item.state == '已下載'
    assert item.progress == 100
    assert item.error == ''


@pytest.mark.parametrize(('url', 'provided_evidence', 'expected_evidence'), [
    (
        'https://missav.ai/sample-001-chinese-subtitle',
        (),
        ('missav-url-chinese-subtitle',),
    ),
    (
        'https://jable.tv/videos/sample-001/',
        ('jable-category-chinese-subtitle',),
        ('jable-category-chinese-subtitle',),
    ),
    (
        'https://supjav.com/440577.html',
        ('supjav-category-chinese-subtitle',),
        ('supjav-category-chinese-subtitle',),
    ),
])
def test_modern_passes_trusted_source_evidence_and_accepts_source_satisfied_zh(
        monkeypatch, tmp_path, url, provided_evidence, expected_evidence):
    video = tmp_path / 'sample.mp4'
    video.write_bytes(b'video')
    job = FakeDownloadJob(video)
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda _url, _dest: job)
    calls = []

    def fake_generate(path, mode, **kwargs):
        calls.append((
            path, mode, kwargs.get('source_subtitle_evidence')))
        return SubtitleResult(
            (), (), satisfied_by_source=('zh-TW',))

    monkeypatch.setattr(gui_modern, 'generate_subtitles', fake_generate)
    manager = gui_modern.DownloadManager(
        subtitle_mode_getter=lambda: 'zh')
    manager.add_item(
        url,
        state='等待中',
        source_subtitle_evidence=provided_evidence,
    )

    manager._run(url, str(tmp_path), epoch=0)

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    item = manager.get_items()[0]
    assert calls == [(str(video), 'zh', expected_evidence)]
    assert item.state == '已下載'
    assert item.error == ''


def test_modern_keeps_video_and_surfaces_subtitle_failure(monkeypatch, tmp_path):
    video = tmp_path / 'sample.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda _url, _dest: FakeDownloadJob(video))
    monkeypatch.setattr(
        gui_modern, 'generate_subtitles',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('offline')))
    manager = gui_modern.DownloadManager(subtitle_mode_getter=lambda: 'zh')
    manager.add_item('https://jable.tv/videos/sample/', state='等待中')

    manager._run('https://jable.tv/videos/sample/', str(tmp_path), epoch=0)

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0
        and manager.get_items()[0].state == '已下載')
    item = manager.get_items()[0]
    assert item.state == '已下載'
    assert 'offline' in item.error
    assert os.path.isfile(video)


def test_modern_no_speech_is_a_non_error_terminal_result(monkeypatch, tmp_path):
    video = tmp_path / 'silent.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda _url, _dest: FakeDownloadJob(video))
    monkeypatch.setattr(
        gui_modern, 'generate_subtitles',
        lambda *_args, **_kwargs: SubtitleResult((), (), no_speech=True))
    manager = gui_modern.DownloadManager(subtitle_mode_getter=lambda: 'ja')
    manager.add_item('https://jable.tv/videos/silent/', state='等待中')

    manager._run(
        'https://jable.tv/videos/silent/', str(tmp_path), epoch=0)

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    item = manager.get_items()[0]
    assert item.state == '未偵測到日語語音'
    assert item.progress == 100
    assert item.error == ''


def test_modern_empty_subtitle_result_is_not_silent_success(
        monkeypatch, tmp_path):
    video = tmp_path / 'empty.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda _url, _dest: FakeDownloadJob(video))
    monkeypatch.setattr(
        gui_modern, 'generate_subtitles',
        lambda *_args, **_kwargs: SubtitleResult((), ()))
    manager = gui_modern.DownloadManager(subtitle_mode_getter=lambda: 'ja')
    manager.add_item('https://jable.tv/videos/empty/', state='等待中')

    manager._run(
        'https://jable.tv/videos/empty/', str(tmp_path), epoch=0)

    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    item = manager.get_items()[0]
    assert item.state == '未完成'
    assert item.error
    assert locales.T('subtitle_empty_result') in item.error


def test_modern_download_slot_is_released_while_subtitles_run(
        monkeypatch, tmp_path):
    first_url = 'https://jable.tv/videos/first/'
    second_url = 'https://jable.tv/videos/second/'
    first_video = tmp_path / 'first.mp4'
    second_video = tmp_path / 'second.mp4'
    first_video.write_bytes(b'first')
    second_video.write_bytes(b'second')

    subtitle_started = threading.Event()
    release_subtitle = threading.Event()
    second_download_started = threading.Event()

    class SequencedJob(FakeDownloadJob):
        def __init__(self, video_path, download_started=None):
            super().__init__(video_path)
            self._download_started = download_started

        def start_download(self):
            if self._download_started:
                self._download_started.set()
            return super().start_download()

    jobs = {
        first_url: SequencedJob(first_video),
        second_url: SequencedJob(second_video, second_download_started),
    }
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda url, _dest: jobs[url])

    def slow_subtitles(path, _mode, progress_callback=None,
                       cancel_check=None):
        if path == str(first_video):
            subtitle_started.set()
            assert release_subtitle.wait(3)
        return SubtitleResult((), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', slow_subtitles)
    manager = gui_modern.DownloadManager(
        max_concurrent=1, subtitle_mode_getter=lambda: 'ja')

    manager.enqueue(first_url, str(tmp_path))
    assert subtitle_started.wait(2)
    manager.enqueue(second_url, str(tmp_path))
    try:
        assert second_download_started.wait(1), (
            'the completed download kept its slot while subtitles were running')
    finally:
        release_subtitle.set()
    assert _wait_until(
        lambda: manager.active_count == 0
        and manager.pending_count == 0
        and manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)


def test_modern_pending_download_queue_advances_after_refactor(
        monkeypatch, tmp_path):
    first_url = 'https://jable.tv/videos/first/'
    second_url = 'https://jable.tv/videos/second/'
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    class BlockingJob(FakeDownloadJob):
        def __init__(self, path, started, release=None):
            super().__init__(path)
            self.started = started
            self.release = release

        def start_download(self):
            self.started.set()
            if self.release:
                assert self.release.wait(3)
            return super().start_download()

    jobs = {
        first_url: BlockingJob(
            tmp_path / 'first.mp4', first_started, release_first),
        second_url: BlockingJob(tmp_path / 'second.mp4', second_started),
    }
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda url, _dest: jobs[url])
    manager = gui_modern.DownloadManager(
        max_concurrent=1, subtitle_mode_getter=lambda: 'none')

    manager.enqueue(first_url, str(tmp_path))
    assert first_started.wait(2)
    manager.enqueue(second_url, str(tmp_path))
    assert manager.active_count == 1
    assert manager.pending_count == 1

    release_first.set()
    assert second_started.wait(2)
    assert _wait_until(
        lambda: manager.active_count == 0 and manager.pending_count == 0)
    assert all(item.state == '已下載' for item in manager.get_items())


def test_modern_subtitle_queue_is_serial_and_dedupes_inflight_urls(
        monkeypatch, tmp_path):
    urls = [
        'https://jable.tv/videos/one/',
        'https://jable.tv/videos/two/',
        'https://jable.tv/videos/three/',
    ]
    paths = {
        url: tmp_path / f'{index}.mp4'
        for index, url in enumerate(urls)
    }
    for path in paths.values():
        path.write_bytes(b'video')

    lock = threading.Lock()
    all_downloaded = threading.Event()
    first_subtitle_started = threading.Event()
    release_subtitle = threading.Event()
    download_calls = []
    subtitle_calls = []

    class TrackedJob(FakeDownloadJob):
        def __init__(self, url):
            super().__init__(paths[url])
            self.url = url

        def start_download(self):
            with lock:
                download_calls.append(self.url)
                if len(download_calls) == len(urls):
                    all_downloaded.set()
            return super().start_download()

    jobs = {url: TrackedJob(url) for url in urls}
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda url, _dest: jobs[url])

    def serial_subtitles(path, _mode, progress_callback=None,
                         cancel_check=None):
        with lock:
            subtitle_calls.append(path)
            first = len(subtitle_calls) == 1
        if first:
            first_subtitle_started.set()
            assert release_subtitle.wait(3)
        return SubtitleResult((path + '.ja.srt',), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', serial_subtitles)
    manager = gui_modern.DownloadManager(
        max_concurrent=3, subtitle_mode_getter=lambda: 'ja')

    for url in urls:
        manager.enqueue(url, str(tmp_path))

    assert first_subtitle_started.wait(2)
    assert all_downloaded.wait(2)
    assert _wait_until(
        lambda: manager.active_count == 0
        and manager.pending_count == 0
        and manager.subtitle_active_count == 1
        and manager.subtitle_pending_count == 2)
    assert len(subtitle_calls) == 1

    # Neither the active subtitle URL nor a queued subtitle URL may be
    # downloaded again.
    manager.enqueue(urls[0], str(tmp_path))
    manager.enqueue(urls[1], str(tmp_path))
    time.sleep(0.05)
    assert sorted(download_calls) == sorted(urls)

    release_subtitle.set()
    assert _wait_until(
        lambda: manager.subtitle_active_count == 0
        and manager.subtitle_pending_count == 0)
    assert len(subtitle_calls) == len(urls)
    assert all(item.state == '已下載' for item in manager.get_items())


def test_modern_cancel_all_cancels_queued_subtitles_and_blocks_stale_finish(
        monkeypatch, tmp_path):
    urls = [
        'https://jable.tv/videos/first/',
        'https://jable.tv/videos/second/',
    ]
    paths = {
        url: tmp_path / f'{index}.mp4'
        for index, url in enumerate(urls)
    }
    for path in paths.values():
        path.write_bytes(b'video')
    jobs = {url: FakeDownloadJob(paths[url]) for url in urls}
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite',
        lambda url, _dest: jobs[url])

    subtitle_started = threading.Event()
    release_subtitle = threading.Event()
    calls = []

    def stubborn_subtitles(path, _mode, progress_callback=None,
                           cancel_check=None):
        calls.append(path)
        subtitle_started.set()
        assert release_subtitle.wait(3)
        progress_callback('translate_zh', 55)
        return SubtitleResult((), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', stubborn_subtitles)
    manager = gui_modern.DownloadManager(
        max_concurrent=2, subtitle_mode_getter=lambda: 'ja')
    for url in urls:
        manager.enqueue(url, str(tmp_path))

    assert subtitle_started.wait(2)
    assert _wait_until(lambda: manager.subtitle_pending_count == 1)
    manager.cancel_all()

    assert manager.active_count == 0
    assert manager.pending_count == 0
    assert manager.subtitle_active_count == 1
    assert manager.subtitle_pending_count == 0
    assert all(item.state == '已取消' for item in manager.get_items())
    assert all(job._cancel_job for job in jobs.values())

    release_subtitle.set()
    assert _wait_until(lambda: manager.subtitle_active_count == 0)
    assert len(calls) == 1
    assert all(item.state == '已取消' for item in manager.get_items())
    assert all(item.progress == 0 for item in manager.get_items())


def test_modern_cancel_blocks_stale_download_progress(monkeypatch, tmp_path):
    url = 'https://jable.tv/videos/stale-progress/'
    video = tmp_path / 'stale-progress.mp4'
    video.write_bytes(b'video')
    download_started = threading.Event()
    release_download = threading.Event()

    class StubbornDownloadJob(FakeDownloadJob):
        def start_download(self):
            download_started.set()
            assert release_download.wait(3)
            if self._progress_callback:
                self._progress_callback(7, 10, 2048)
            return True

    job = StubbornDownloadJob(video)
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite', lambda _url, _dest: job)
    manager = gui_modern.DownloadManager(subtitle_mode_getter=lambda: 'none')
    manager.enqueue(url, str(tmp_path))

    assert download_started.wait(2)
    manager.cancel_all()
    item = manager.get_items()[0]
    assert item.state == '已取消'
    assert item.progress == 0
    assert item.speed == ''

    release_download.set()
    assert _wait_until(lambda: manager.active_count == 0)
    assert item.state == '已取消'
    assert item.progress == 0
    assert item.speed == ''


def test_modern_cancellation_dominates_stale_final_completion(tmp_path):
    download_url = 'https://jable.tv/videos/stale-download-finish/'
    download_manager = gui_modern.DownloadManager()
    download_item = download_manager.add_item(
        download_url, state='下載中')
    download_task = gui_modern._DownloadTask(
        download_url, str(tmp_path), download_manager._cancel_epoch)
    with download_manager._lock:
        download_manager._active[download_url] = download_task

    download_manager.cancel_all(cleanup=False)
    download_manager._complete_download(
        download_task, '已下載', progress=100)

    assert download_item.state == '已取消'
    assert download_item.progress == 0

    subtitle_url = 'https://jable.tv/videos/stale-subtitle-finish/'
    subtitle_manager = gui_modern.DownloadManager()
    subtitle_item = subtitle_manager.add_item(
        subtitle_url, state='字幕翻譯中')
    subtitle_task = gui_modern._DownloadTask(
        subtitle_url, str(tmp_path), subtitle_manager._cancel_epoch)
    with subtitle_manager._lock:
        subtitle_manager._subtitle_active[subtitle_url] = subtitle_task

    subtitle_manager.cancel_all(cleanup=False)
    subtitle_manager._complete_subtitle(
        subtitle_task, '已下載', progress=100)

    assert subtitle_item.state == '已取消'
    assert subtitle_item.progress == 0


def test_modern_remove_active_subtitle_does_not_restore_removed_item(
        monkeypatch, tmp_path):
    url = 'https://jable.tv/videos/remove/'
    video = tmp_path / 'remove.mp4'
    video.write_bytes(b'video')
    job = FakeDownloadJob(video)
    monkeypatch.setattr(
        gui_modern.M3U8Sites, 'CreateSite', lambda _url, _dest: job)

    subtitle_started = threading.Event()
    release_subtitle = threading.Event()

    def slow_subtitles(*_args, **_kwargs):
        subtitle_started.set()
        assert release_subtitle.wait(3)
        return SubtitleResult((), ())

    monkeypatch.setattr(gui_modern, 'generate_subtitles', slow_subtitles)
    manager = gui_modern.DownloadManager(subtitle_mode_getter=lambda: 'ja')
    manager.enqueue(url, str(tmp_path))

    assert subtitle_started.wait(2)
    manager.remove_item(url)
    assert manager.get_items() == []
    assert job._cancel_job is True

    release_subtitle.set()
    assert _wait_until(lambda: manager.subtitle_active_count == 0)
    assert manager.get_items() == []


def test_smalltool_runs_subtitles_before_marking_seen(monkeypatch, tmp_path):
    video_path = tmp_path / 'sample.mp4'
    video_path.write_bytes(b'video')
    job = FakeDownloadJob(video_path)
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(
        jable_smalltool.M3U8Sites, 'CreateSite', lambda _url, _dest: job)
    calls = []
    subtitle_progress = []

    def fake_generate(path, mode, progress_callback=None, cancel_check=None):
        calls.append((path, mode))
        progress_callback('transcribe_ja', 0)
        subtitle_progress.append(worker.get_progress())
        progress_callback('translate_zh', 100)
        return SubtitleResult((str(tmp_path / 'sample.zh-TW.srt'),), ())

    monkeypatch.setattr(jable_smalltool, 'generate_subtitles', fake_generate)
    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._subtitle_mode = 'zh'
    marked = []
    worker._mark_seen = lambda url, title, **kwargs: marked.append((url, title))
    sample = {
        'url': 'https://missav.ai/sample', 'title': 'sample', '_site': 'MissAV'}

    result = worker._download_one(sample, str(tmp_path))

    assert result is None
    assert calls == [(str(video_path), 'zh')]
    assert subtitle_progress
    assert subtitle_progress[0][:3] == (0, 100, -1)
    assert marked == [('https://missav.ai/sample', 'sample')]
    assert worker.get_progress() is None


@pytest.mark.parametrize(('sample', 'expected_evidence'), [
    ({
        'url': 'https://jable.tv/videos/sample-001/',
        'title': 'sample',
        '_site': 'JableTV',
        '_target_id': 'category:chinese-subtitle',
    }, ('jable-category-chinese-subtitle',)),
    ({
        'url': 'https://missav.ai/sample-001',
        'title': 'sample',
        '_site': 'MissAV',
        '_target_id': 'feed:chinese-subtitle',
    }, ('missav-category-chinese-subtitle',)),
    ({
        'url': 'https://supjav.com/440577.html',
        'title': 'sample',
        '_site': 'SupJav',
        '_target_id': 'category:chinese-subtitles',
    }, ('supjav-category-chinese-subtitle',)),
])
def test_smalltool_passes_category_evidence_and_marks_source_satisfied_zh_seen(
        monkeypatch, tmp_path, sample, expected_evidence):
    video_path = tmp_path / 'sample.mp4'
    video_path.write_bytes(b'video')
    job = FakeDownloadJob(video_path)
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(
        jable_smalltool.M3U8Sites, 'CreateSite',
        lambda _url, _dest: job)
    calls = []

    def fake_generate(path, mode, **kwargs):
        calls.append((
            path, mode, kwargs.get('source_subtitle_evidence')))
        return SubtitleResult(
            (), (), satisfied_by_source=('zh-TW',))

    monkeypatch.setattr(
        jable_smalltool, 'generate_subtitles', fake_generate)
    logs = []
    worker = jable_smalltool.SmallToolWorker(logs.append)
    worker._subtitle_mode = 'zh'
    marked = []
    worker._mark_seen = (
        lambda url, title, **kwargs:
            marked.append((url, title, kwargs.get('video'))))
    result = worker._download_one(sample, str(tmp_path))

    assert result is None
    assert calls == [(str(video_path), 'zh', expected_evidence)]
    assert marked == [(sample['url'], sample['title'], sample)]
    assert any(
        locales.T('subtitle_source_chinese_skip') in line
        for line in logs)
    assert not any('[SUBTITLE-ERR]' in line for line in logs)
    assert locales.T('subtitle_ready', count=0) not in logs


def test_smalltool_no_speech_is_logged_and_marked_seen(monkeypatch, tmp_path):
    video_path = tmp_path / 'silent.mp4'
    video_path.write_bytes(b'video')
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(
        jable_smalltool.M3U8Sites, 'CreateSite',
        lambda _url, _dest: FakeDownloadJob(video_path))
    monkeypatch.setattr(
        jable_smalltool, 'generate_subtitles',
        lambda *_args, **_kwargs: SubtitleResult((), (), no_speech=True))
    logs = []
    worker = jable_smalltool.SmallToolWorker(logs.append)
    worker._subtitle_mode = 'ja'
    marked = []
    worker._mark_seen = lambda url, title, **kwargs: marked.append((url, title))
    sample = {
        'url': 'https://missav.ai/silent',
        'title': 'silent',
        '_site': 'MissAV',
    }

    result = worker._download_one(sample, str(tmp_path))

    assert result is None
    assert any(locales.T('subtitle_no_speech') in line for line in logs)
    assert not any('[SUBTITLE-ERR]' in line for line in logs)
    assert marked == [('https://missav.ai/silent', 'silent')]


def test_smalltool_empty_subtitle_result_retries_without_marking_seen(
        monkeypatch, tmp_path):
    video_path = tmp_path / 'empty.mp4'
    video_path.write_bytes(b'video')
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(
        jable_smalltool.M3U8Sites, 'CreateSite',
        lambda _url, _dest: FakeDownloadJob(video_path))
    monkeypatch.setattr(
        jable_smalltool, 'generate_subtitles',
        lambda *_args, **_kwargs: SubtitleResult((), ()))
    logs = []
    worker = jable_smalltool.SmallToolWorker(logs.append)
    worker._subtitle_mode = 'ja'
    marked = []
    worker._mark_seen = lambda *args, **kwargs: marked.append(args)

    result = worker._download_one(
        {
            'url': 'https://missav.ai/empty',
            'title': 'empty',
            '_site': 'MissAV',
        },
        str(tmp_path))

    assert result == 'subtitle_failed'
    assert marked == []
    assert any(locales.T('subtitle_empty_result') in line for line in logs)


def test_smalltool_retries_when_subtitle_generation_fails(monkeypatch, tmp_path):
    video_path = tmp_path / 'sample.mp4'
    video_path.write_bytes(b'video')
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(
        jable_smalltool.M3U8Sites, 'CreateSite',
        lambda _url, _dest: FakeDownloadJob(video_path))
    monkeypatch.setattr(
        jable_smalltool, 'generate_subtitles',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('offline')))
    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._subtitle_mode = 'all'
    marked = []
    worker._mark_seen = lambda *args, **kwargs: marked.append(args)

    result = worker._download_one(
        {'url': 'https://missav.ai/sample', 'title': 'sample', '_site': 'MissAV'},
        str(tmp_path))

    assert result == 'subtitle_failed'
    assert marked == []
    assert os.path.isfile(video_path)
