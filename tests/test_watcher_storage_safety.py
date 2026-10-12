import errno
from datetime import datetime, timezone

from uav_downloader.apps import watch
from uav_downloader.apps.watch_categories import find_target
from uav_downloader.core.storage import StorageWriteError


def test_destination_io_failure_halts_watcher_and_preserves_chunks(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    logs, statuses = [], []
    worker = watch.SmallToolWorker(logs.append, lambda key, _color: statuses.append(key))
    class Job:
        def is_url_vaildate(self):
            return True
        def start_download(self):
            raise StorageWriteError(tmp_path, OSError(errno.EIO, 'device I/O error'))
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: Job())
    worker._cleanup_temp = lambda _job: (_ for _ in ()).throw(
        AssertionError('storage failure must preserve validated chunks'))
    assert worker._download_one(
        {'url': 'https://missav.ws/test-001', 'title': 'TEST-001'}, str(tmp_path)) == 'storage_failed'
    assert worker._stop.is_set()
    assert worker._seen == {}
    assert 'st_storage_failed' in statuses
    assert any('[STORAGE-ERR]' in line for line in logs)


def test_watcher_does_not_start_next_video_after_storage_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    worker = watch.SmallToolWorker(lambda _line: None)
    worker._fetch_page_for_site = lambda _site, url: ([] if 'page=2' in url else [
        {'url': 'https://missav.ws/test-001'},
        {'url': 'https://missav.ws/test-002'}])
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 10, 10, tzinfo=timezone.utc), '2026-10-10')
    calls = []
    class Job:
        def is_url_vaildate(self):
            return True
        def start_download(self):
            raise StorageWriteError(tmp_path, OSError(errno.ENOSPC, 'disk full'))
        def is_target_video_exist(self):
            return False
    def create(url, _dest):
        calls.append(url)
        return Job()
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', create)
    target = find_target('MissAV', legacy_name='最近更新')
    cfg = {'output_folder': str(tmp_path), 'baseline_date': '2026-01-01',
           'first_run_done': False, 'version_preference': 'standard',
           'selected_targets': [{'site': 'MissAV', 'id': target['id']}]}
    assert worker._scan_and_download(cfg) is False
    assert len(calls) == 1
    assert cfg['first_run_done'] is False
    assert worker._seen == {}


def test_subtitle_storage_error_stops_monitoring_without_marking_success(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    worker = watch.SmallToolWorker(lambda _line: None)
    worker._subtitle_mode = 'zh'
    class Job:
        _cancel_job = False
        def is_url_vaildate(self):
            return True
        def start_download(self):
            return True
        def _get_video_savename(self):
            return str(tmp_path / 'TEST-001.mp4')
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: Job())
    def fail_subtitle(*_args, **_kwargs):
        raise StorageWriteError(tmp_path, OSError(errno.ENOSPC, 'disk full'))
    monkeypatch.setattr(watch, 'generate_subtitles', fail_subtitle)
    assert worker._download_one(
        {'url': 'https://missav.ws/test-001'}, str(tmp_path)) == 'storage_failed'
    assert worker._stop.is_set()
    assert worker._seen == {}


def test_initial_destination_creation_error_stops_monitoring(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    worker = watch.SmallToolWorker(lambda _line: None)
    destination = str(tmp_path / 'device')
    def fail_creation(path, **_kwargs):
        assert path == destination
        raise OSError(errno.EIO, 'device unavailable')
    monkeypatch.setattr(watch.os, 'makedirs', fail_creation)
    target = find_target('MissAV', legacy_name='最近更新')
    cfg = {'output_folder': destination, 'baseline_date': '2026-01-01',
           'first_run_done': False,
           'selected_targets': [{'site': 'MissAV', 'id': target['id']}]}
    assert worker._scan_and_download(cfg) is False
    assert worker._stop.is_set()
    assert cfg['first_run_done'] is False
