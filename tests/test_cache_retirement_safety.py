"""Cancellation must not delete an active writer's cache or unrelated files."""
import threading

from uav_downloader.apps.watch import SmallToolWorker
from uav_downloader.sites.base import M3U8Crawler
from uav_downloader.core.hls_cache import cache_name, output_lease, select_cache
import pytest


def test_cancel_cleanup_waits_for_transfer_owner(tmp_path):
    digest = 'f' * 64
    cache = tmp_path / cache_name('fixture', 'fixture', digest)
    with output_lease(tmp_path, 'fixture'):
        select_cache(tmp_path, 'fixture', 'fixture', digest, 1)
    segment = cache / '000000.mp4'
    segment.write_bytes(b'fixture')
    user_file = cache / 'keep.txt'
    user_file.write_text('not created by downloader')
    job = M3U8Crawler('', str(tmp_path))
    job._dest_folder, job._temp_folder = str(tmp_path), str(cache)
    job._dirName = 'fixture'
    job._targetName, job._playlist_fingerprint = 'fixture', digest
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
    assert not Path(old._seg_savename(0)).exists()


def test_retry_with_new_signed_token_and_same_timeline_reuses_good_bytes(tmp_path):
    from test_hls_segment_recovery import _job, PACKET
    from uav_downloader.sites.base import DownloadIncompleteError
    manifest = '#EXTM3U\n#EXTINF:1,\na?token=old\n#EXTINF:1,\nb?token=old\n'
    old, _ = _job(tmp_path, manifest, {'a': (200, PACKET), 'b': (403, b'')})
    with pytest.raises(DownloadIncompleteError):
        old.start_download()
    new, calls = _job(tmp_path, manifest.replace('old','new'), {'b': (200, PACKET)})
    assert new.start_download() and calls == ['playlist', 'b']


@pytest.mark.parametrize('site, query', [('huangguo', 'sig={}'), ('supjav', 't={}&s=opaque')])
def test_refresh_signed_master_reuses_segments(tmp_path, site, query):
    from test_hls_segment_recovery import _job, PACKET
    from uav_downloader.sites.base import DownloadIncompleteError
    from uav_downloader.sites.huangguo import SiteHuangguo
    from uav_downloader.sites.supjav import SiteSupJav
    for n in (1, 2):
        job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n#EXTINF:1,\nb\n',
                          {'a': (200, PACKET), 'b': (403, b'')})
        job.__class__ = {'huangguo':SiteHuangguo, 'supjav':SiteSupJav}[site]
        job._m3u8url = 'https://example.invalid/playlist.m3u8?' + query.format(n)
        original = job._media_get
        def get(url, **kw):
            resp = original(url.replace('playlist.m3u8', 'playlist'), **kw)
            if 'playlist.m3u8' in url:
                resp.url = url
            return resp
        job._media_get = get
        with pytest.raises(DownloadIncompleteError):
            M3U8Crawler.start_download(job)
        assert calls.count('a') == (n == 1)


def test_unattended_retries_use_bounded_disk_for_unknown_rotating_selectors(tmp_path):
    from test_hls_segment_recovery import _job, PACKET
    from uav_downloader.sites.base import DownloadIncompleteError
    for index in range(6):
        manifest = f'#EXTM3U\n#EXTINF:1,\na?v={index}\n#EXTINF:1,\nb\n'
        job, calls = _job(tmp_path, manifest, {'a': (200, PACKET), 'b': (403, b'')})
        with pytest.raises(DownloadIncompleteError):
            job.start_download()
        assert calls.count('a') == 1
        segments = list(tmp_path.glob('.uav-hls-*/*.mp4'))
        assert len(segments) == 1 and sum(p.stat().st_size for p in segments) == len(PACKET)


def test_cache_switch_write_failure_preserves_control_and_unrelated_files(tmp_path, monkeypatch):
    from pathlib import Path
    from uav_downloader.core import hls_cache
    from uav_downloader.core.storage import StorageWriteError
    import errno
    with output_lease(tmp_path, 'fixture'):
        old = Path(select_cache(tmp_path, 'slug', 'fixture', 'a'*64, 1))
        (old/'000000.mp4').write_bytes(b'owned')
        (old/'000099.mp4').write_bytes(b'not ours')
        (old/'notes.txt').write_text('keep')
        cursor = next((tmp_path/'.uav-locks').glob('*.json'))
        proof = cursor.read_bytes()
        with monkeypatch.context() as patch:
            def full(*_):
                raise OSError(errno.ENOSPC, 'synthetic disk full')
            patch.setattr(hls_cache.os, 'replace', full)
            with pytest.raises(StorageWriteError):
                select_cache(tmp_path, 'slug', 'fixture', 'b'*64, 1)
        assert cursor.read_bytes() == proof
        new = Path(select_cache(tmp_path, 'slug', 'fixture', 'b'*64, 1))
        assert new != old and list(new.iterdir()) == []
        assert (old/'000099.mp4').read_bytes() == b'not ours'
        assert (old/'notes.txt').read_text() == 'keep'


def test_legacy_numbered_files_not_adopted_or_removed(tmp_path):
    from test_hls_segment_recovery import _job, PACKET
    old = tmp_path/'fixture'
    old.mkdir()
    (old/'000000.mp4').write_bytes(PACKET[:-1] + b'L')
    job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n', {'a': (200, PACKET)})
    assert job.start_download() and calls == ['playlist', 'a']
    assert (old/'000000.mp4').read_bytes() == PACKET[:-1] + b'L'
