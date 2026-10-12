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
