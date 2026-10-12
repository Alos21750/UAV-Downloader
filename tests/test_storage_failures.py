"""Local storage failures must not enter network retry/fallback loops (#54)."""
import errno
import types
import pytest

from uav_downloader.core.storage import StorageWriteError, storage_open
from uav_downloader.sites import base, supjav
from uav_downloader.cli import headless


def test_storage_open_classifies_open_failure(monkeypatch, tmp_path):
    def broken(*args, **kwargs):
        raise OSError(errno.EIO, 'device unavailable')
    monkeypatch.setattr('builtins.open', broken)
    with pytest.raises(StorageWriteError, match='device unavailable'):
        with storage_open(tmp_path / 'video.part', 'wb'):
            pass


def test_storage_open_does_not_classify_network_errors(tmp_path):
    with pytest.raises(ConnectionError) as caught:
        with storage_open(tmp_path / 'video.part', 'wb'):
            raise ConnectionError('network reset')
    assert not isinstance(caught.value, StorageWriteError)


def test_storage_open_rejects_short_write(monkeypatch, tmp_path):
    fake = types.SimpleNamespace(write=lambda data: len(data) - 1, close=lambda: None)
    monkeypatch.setattr('builtins.open', lambda *a, **kw: fake)
    with pytest.raises(StorageWriteError, match='short write'):
        with storage_open(tmp_path / 'video.part', 'wb') as target:
            target.write(b'video')


def test_hls_write_error_is_fatal_not_pending_retry(monkeypatch, tmp_path):
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._storage_error = None
    job._seg_savename = lambda index: str(tmp_path / 'segment.ts')
    job._m3u8_headers = lambda: {}
    job._key_content = None
    job._transform_segment = lambda data: data
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: types.SimpleNamespace(
        status_code=200, content=b'\x47' + b'\0' * 187))
    monkeypatch.setattr(base, '_is_valid_ts_segment', lambda data: True)
    def broken(*args, **kwargs):
        raise StorageWriteError(str(tmp_path), OSError(errno.EINVAL, 'device offline'))
    monkeypatch.setattr(base, 'storage_open', broken)
    with pytest.raises(StorageWriteError):
        job._scrape((0, 'https://example.test/segment.ts'))
    assert isinstance(job._storage_error, StorageWriteError)


def test_direct_storage_error_never_falls_back_to_serial(monkeypatch, tmp_path):
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._cancel_job = False
    job._direct_referer = 'https://example.test/'
    job._create_dest_folder = lambda: None
    job.is_target_video_exist = lambda: False
    job._get_video_savename = lambda: str(tmp_path / 'video.mp4')
    def broken(*args):
        raise StorageWriteError(str(tmp_path), OSError(errno.ENOSPC, 'full'))
    job._download_direct_ranges = broken
    job._download_direct_serial = lambda *args: pytest.fail('must not retry disk failure')
    with pytest.raises(StorageWriteError):
        job._download_direct()


def test_cli_stops_batch_on_storage_error(monkeypatch, tmp_path):
    calls = []
    class Site:
        def is_url_vaildate(self):
            return True
        def start_download(self):
            raise StorageWriteError(str(tmp_path), OSError(errno.EIO, 'offline'))
    def create(*args, **kwargs):
        calls.append(args)
        return Site()
    monkeypatch.setattr(headless, 'gather_urls', lambda: ['one', 'two'])
    monkeypatch.setattr(headless.M3U8Sites, 'CreateSite', create)
    monkeypatch.setenv('DOWNLOAD_DIR', str(tmp_path))
    assert headless.main() == 1
    assert len(calls) == 1


def test_direct_serial_storage_failure_is_not_retried(monkeypatch, tmp_path):
    calls = []
    class Response:
        status_code = 200
        headers = {'content-length': '5'}
        def iter_content(self, **kwargs):
            yield b'video'
        def close(self):
            pass
    def get(*args, **kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr(supjav, '_get_session', lambda: types.SimpleNamespace(get=get))
    def broken(*args, **kwargs):
        raise StorageWriteError(str(tmp_path), OSError(errno.EIO, 'offline'))
    monkeypatch.setattr(supjav, 'storage_open', broken)
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._cancel_job = False
    job._direct_url = 'https://example.test/video'
    with pytest.raises(StorageWriteError):
        job._download_direct_serial(str(tmp_path / 'video.part'), '', 0)
    assert len(calls) == 1


def test_browser_storage_failure_stops_remaining_queue(monkeypatch, tmp_path):
    from uav_downloader.apps import browse
    manager = browse.DownloadManager(max_concurrent=1)
    started = []
    monkeypatch.setattr(manager, '_start_download_thread', started.append)
    cancelled = []
    class Site:
        _cancel_job = False
        def is_url_vaildate(self):
            return True
        def target_name(self):
            return 'video'
        def start_download(self):
            raise StorageWriteError(str(tmp_path), OSError(errno.EIO, 'offline'))
        def cancel_download(self, cleanup=True):
            cancelled.append(cleanup)
            self._cancel_job = True
    monkeypatch.setattr(browse.M3U8Sites, 'CreateSite', lambda *a: Site())
    for url in ('first', 'second'):
        manager.add_item(url, dest=str(tmp_path))
        manager.enqueue(url, str(tmp_path))
    assert len(started) == 1
    manager._run_download(started[0])
    assert len(started) == 1
    items = {item.url: item for item in manager.get_items()}
    assert items['first'].state == '未完成'
    assert 'storage I/O failed' in items['first'].error
    assert items['second'].state == '已取消'
    assert cancelled == [False]


def test_remux_disk_full_does_not_start_raw_fallback(monkeypatch, tmp_path):
    class Process:
        returncode = 1
        def wait(self, **kwargs):
            return 1
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(base, '_ffmpeg_safe_dir', lambda path: path)
    def run(*args, **kwargs):
        kwargs['stderr'].write(b'Error writing trailer: No space left on device\n')
        return Process()
    monkeypatch.setattr(base.subprocess, 'Popen', run)
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    with pytest.raises(StorageWriteError, match='No space left'):
        job._remux_to_mp4(str(tmp_path / 'in.ts'), str(tmp_path / 'out.mp4'), str(tmp_path))


def test_failed_segment_write_never_publishes_truncated_chunk(monkeypatch, tmp_path):
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._storage_error = None
    final = tmp_path / 'segment.ts'
    job._seg_savename = lambda index: str(final)
    job._m3u8_headers = lambda: {}
    job._key_content = None
    job._transform_segment = lambda data: data
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: types.SimpleNamespace(
        status_code=200, content=b'video'))
    monkeypatch.setattr(base, '_is_valid_ts_segment', lambda data: True)
    class Broken:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def write(self, data):
            (tmp_path / 'segment.ts.part').write_bytes(data[:1])
            raise StorageWriteError(str(final), OSError(errno.EIO, 'offline'))
    monkeypatch.setattr(base, 'storage_open', lambda *a, **kw: Broken())
    with pytest.raises(StorageWriteError):
        job._scrape((0, 'https://example.test/seg.ts'))
    assert not final.exists()
    assert (tmp_path / 'segment.ts.part').exists()


def test_subtitle_reserve_error_is_fatal_storage_error():
    from uav_downloader.subtitles.engine import SubtitleStorageError
    assert isinstance(SubtitleStorageError('Not enough free disk space'), StorageWriteError)


def test_subtitle_atomic_write_classifies_device_failure(monkeypatch, tmp_path):
    from uav_downloader.subtitles import engine
    def broken(*args, **kwargs):
        raise OSError(errno.EIO, 'device offline')
    monkeypatch.setattr(engine.tempfile, 'mkstemp', broken)
    with pytest.raises(StorageWriteError):
        engine._atomic_write_text(str(tmp_path / 'video.en.srt'), 'subtitle')
