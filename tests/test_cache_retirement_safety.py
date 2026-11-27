"""Cancellation must not delete an active writer's cache or unrelated files."""
import threading

from uav_downloader.apps.watch import SmallToolWorker
from uav_downloader.sites.base import M3U8Crawler
from uav_downloader.core.hls_cache import cache_name, output_lease
import pytest


def test_cancel_cleanup_waits_for_transfer_owner(tmp_path):
    cache = tmp_path / cache_name('fixture', 'fixture', 'manifest')
    cache.mkdir()
    segment = cache / '000000.mp4'
    segment.write_bytes(b'fixture')
    user_file = cache / 'keep.txt'
    user_file.write_text('not created by downloader')
    job = M3U8Crawler('', str(tmp_path))
    job._dest_folder, job._temp_folder = str(tmp_path), str(cache)
    job._dirName = 'fixture'
    job._targetName, job._playlist_fingerprint = 'fixture', 'manifest'
    job._tsList = ['offline']
    entered, release = threading.Event(), threading.Event()
    job._create_dest_folder = job.download_image = lambda: None
    job.is_target_video_exist = lambda: False
    job._create_temp_folder = job._create_m3u8 = lambda: None

    def writer():
        entered.set()
        assert release.wait(5)

    job._prepareCrawl = writer
    result = []
    thread = threading.Thread(target=lambda: result.append(job.start_download()))
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match='Another download'):
            with output_lease(tmp_path, 'fixture'):
                pytest.fail('another writer acquired the active output')
        job.cancel_download()
        assert segment.is_file() and user_file.is_file()
    finally:
        release.set()
        thread.join(6)
    assert not thread.is_alive() and result == [False]
    assert not segment.exists()
    assert user_file.read_text() == 'not created by downloader'
    segment.write_bytes(b'next generation')
    job.cancel_download()
    assert segment.read_bytes() == b'next generation'


def test_watcher_does_not_recursively_remove_user_destination(tmp_path, monkeypatch):
    from uav_downloader.apps import watch
    user_file = tmp_path / 'keep.txt'
    user_file.write_text('not created by downloader')
    job = M3U8Crawler('', str(tmp_path))
    job._dest_folder = job._temp_folder = str(tmp_path)
    job.is_url_vaildate = lambda: True
    job.is_target_video_exist = lambda: False
    job.start_download = lambda: False
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *a, **kw: job)
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    worker = SmallToolWorker(lambda *_: None)
    worker._download_one({'url':'https://missav.ws/test-001','title':'TEST-001'}, str(tmp_path))
    assert user_file.is_file()


@pytest.mark.parametrize('difference', ['source', 'resolution', 'selector', 'duration', 'sequence'])
def test_retry_cannot_mix_two_timelines(tmp_path, difference):
    from pathlib import Path
    from test_hls_segment_recovery import _job, PACKET
    from uav_downloader.sites.base import DownloadIncompleteError

    one, two = PACKET[:-1] + b'A', PACKET[:-1] + b'B'
    manifest = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:1\n#EXTINF:2,\na?v=1\n#EXTINF:2,\nb?v=1\n'
    old, _ = _job(tmp_path, manifest, {'a': (200, one), 'b': (403, b'')})
    old._hls_source_name, old._resolution_pref = 'one', '1080p'
    with pytest.raises(DownloadIncompleteError):
        old.start_download()
    other_manifest = {'selector': manifest.replace('v=1','v=2'),
                      'sequence': manifest.replace('SEQUENCE:1','SEQUENCE:2'),
                      'duration': manifest.replace('INF:2','INF:4')}.get(difference, manifest)
    new, calls = _job(tmp_path, other_manifest, {'a': (200, two), 'b': (200, two)})
    new._hls_source_name = 'two' if difference == 'source' else 'one'
    new._resolution_pref = '720p' if difference == 'resolution' else '1080p'
    assert new.start_download() and set(calls) == {'playlist','a','b'}
    assert new._temp_folder != old._temp_folder
    assert [Path(new._seg_savename(i)).read_bytes() for i in (0, 1)] == [two, two]
    assert Path(old._seg_savename(0)).read_bytes() == one


def test_retry_with_new_signed_token_and_same_timeline_reuses_good_bytes(tmp_path):
    from test_hls_segment_recovery import _job, PACKET
    from uav_downloader.sites.base import DownloadIncompleteError
    manifest = '#EXTM3U\n#EXTINF:1,\na?token=old\n#EXTINF:1,\nb?token=old\n'
    old, _ = _job(tmp_path, manifest, {'a': (200, PACKET), 'b': (403, b'')})
    with pytest.raises(DownloadIncompleteError):
        old.start_download()
    new, calls = _job(tmp_path, manifest.replace('old','new'), {'b': (200, PACKET)})
    assert new.start_download() and calls == ['playlist', 'b']
