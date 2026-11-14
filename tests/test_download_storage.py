"""Resource identity and real filesystem ownership, without network downloads."""
from pathlib import Path
import json
import errno
import os
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import m3u8
import pytest

from uav_downloader.sites.base import M3U8Crawler
from uav_downloader.sites import base, supjav
from uav_downloader.core.download_storage import (
    DownloadStorage, FileOwnership, ResourceBusyError, ResourceChangedError, task_identity, url_fingerprint,
)
from uav_downloader.core import download_storage
from uav_downloader.core.storage import StorageWriteError


class OfflineCrawler(M3U8Crawler):
    website_dirname_pattern = r'https://[^/]+/(?:videos/)?([^/]+)/?$'

    def get_url_infos(self):
        self._targetName = 'SAME-001 shared title'
        self._m3u8url = 'https://cdn.example.test/master.m3u8'


def test_real_crawlers_isolate_cross_site_and_resolution_caches(tmp_path):
    sites = [OfflineCrawler(url, savepath=str(tmp_path), silence=True, resolution=resolution)
             for url, resolution in [
                 ('https://jable.tv/videos/same-001/', '720'),
                 ('https://missav.ai/same-001', '720'),
                 ('https://jable.tv/videos/same-001/', '1080')]]
    assert all(site.is_url_vaildate() for site in sites)
    assert len({Path(site._temp_folder) for site in sites}) == 3
    assert len({site._seg_savename(0) for site in sites}) == 3


def storage(tmp_path, video='same-001', resolution='720'):
    return DownloadStorage(tmp_path, task_identity('https://jable.tv/videos/'+video+'/', video, resolution))


def candidates(tmp_path):
    return lambda attempt, owner: tmp_path / ('video.mp4' if attempt == 0 else f'video_{owner[:8]}_{attempt}.mp4')


def write_segment(store, content=b'controlled segment'):
    path = store.cache / '000000.mp4'
    path.write_bytes(content)
    store.record_segment(0, 'https://cdn.example.test/0.ts', content, '"asset-v1"')
    return path


def test_os_lock_rejects_another_owner_in_same_process(tmp_path):
    a, b = FileOwnership(tmp_path/'resource.lock'), FileOwnership(tmp_path/'resource.lock')
    with a:
        with pytest.raises(ResourceBusyError):
            b.acquire()
    with b:
        assert (tmp_path/'resource.lock').exists()


def test_lock_permission_error_is_storage_failure_not_another_owner(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, 'synthetic permission failure')
    monkeypatch.setattr(download_storage, 'open', denied, raising=False)
    with pytest.raises(StorageWriteError):
        FileOwnership(tmp_path/'resource.lock').acquire()


@pytest.mark.parametrize('crash', [False, True])
def test_os_lock_excludes_other_process_and_recovers_after_exit(tmp_path, crash):
    path = tmp_path/'resource.lock'
    script = """from uav_downloader.core.download_storage import FileOwnership
import sys
with FileOwnership(sys.argv[1]):
    print('owned', flush=True)
    sys.stdin.readline()
"""
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
    process = subprocess.Popen([sys.executable, '-u', '-c', script, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        assert process.stdout.readline().strip() == 'owned'
        with pytest.raises(ResourceBusyError):
            FileOwnership(path).acquire()
        if crash:
            process.kill()
        else:
            process.stdin.write('\n'); process.stdin.flush()
        process.communicate(timeout=5)
        with FileOwnership(path):
            assert path.exists()  # A stale lock file is not stale ownership.
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_mirror_urls_share_task_ownership_but_versions_and_quality_do_not(tmp_path):
    a = storage(tmp_path)
    mirror = DownloadStorage(tmp_path, task_identity('https://fs1.app/videos/same-001/', 'same-001', '720'))
    with a.session():
        with pytest.raises(ResourceBusyError):
            with mirror.session():
                pytest.fail('mirror acquired a second writer')
        with storage(tmp_path, resolution='1080').session():
            pass
        with storage(tmp_path, video='same-001-uncensored-leak').session():
            pass


def test_cleanup_waits_for_active_owner_and_never_deletes_legacy_directory(tmp_path):
    legacy = tmp_path/'same-001'
    legacy.mkdir(); (legacy/'000000.mp4').write_bytes(b'old unproven segment')
    store = storage(tmp_path)
    with store.session():
        store.bind_media(dict(playlist='a'))
        segment = write_segment(store)
        assert store.cleanup() is False
        assert segment.exists()
    assert not segment.exists()
    assert (legacy/'000000.mp4').read_bytes() == b'old unproven segment'


def test_previous_instance_cannot_clean_new_instance_cache(tmp_path):
    first, second = storage(tmp_path), storage(tmp_path)
    with first.session():
        first.bind_media(dict(playlist='a'))
        segment = write_segment(first)
    with second.session():
        second.bind_media(dict(playlist='a'))
        assert second.local_segment_valid(0)
        assert not second.reusable_segment(0, 'https://cdn.example.test/0.ts')
        assert second.revalidate_segment(0, 'https://cdn.example.test/0.ts')
        assert second.reusable_segment(0, 'https://cdn.example.test/0.ts')
    assert first.cleanup() is False
    assert segment.exists()
    assert second.cleanup() is True


def test_cache_manifest_is_required_and_legacy_files_are_preserved(tmp_path):
    store = storage(tmp_path)
    with store.session():
        store.bind_media(dict(playlist='a'))
        segment = write_segment(store)
        old_cache = store.cache
    (old_cache/'manifest.json').unlink()
    restored = storage(tmp_path)
    with restored.session():
        restored.bind_media(dict(playlist='a'))
        assert restored.cache != old_cache
        assert not restored.reusable_segment(0, 'https://cdn.example.test/0.ts')
    assert segment.read_bytes() == b'controlled segment'


def test_changed_playlist_or_key_gets_another_cache(tmp_path):
    a, b = storage(tmp_path), storage(tmp_path)
    with a.session():
        a.bind_media(dict(playlist='a', key_sha256='key-a'))
        segment = write_segment(a)
    with b.session():
        b.bind_media(dict(playlist='a', key_sha256='key-b'))
        assert b.cache != a.cache
        assert not b.reusable_segment(0, 'https://cdn.example.test/0.ts')
    assert segment.exists()


def test_modified_or_unrecorded_segment_is_not_reused(tmp_path):
    store = storage(tmp_path)
    with store.session():
        store.bind_media(dict(playlist='a'))
        segment = write_segment(store, b'original')
        assert store.reusable_segment(0, 'https://cdn.example.test/0.ts')
        segment.write_bytes(b'tampered')
        assert not store.reusable_segment(0, 'https://cdn.example.test/0.ts')
        segment.write_bytes(b'')
        assert not store.reusable_segment(0, 'https://cdn.example.test/0.ts')


def test_same_title_different_resources_get_owned_outputs(tmp_path):
    a, b = storage(tmp_path, 'a-001'), storage(tmp_path, 'b-001')
    with a.session():
        a.bind_media(dict(playlist='a')); a.reserve_output(candidates(tmp_path))
        a.output.write_bytes(b'first resource'); a.publish_output()
        with b.session():
            b.bind_media(dict(playlist='b')); b.reserve_output(candidates(tmp_path))
            assert a.output != b.output
            b.output.write_bytes(b'second resource'); b.publish_output()
    assert a.output.read_bytes() == b'first resource' and b.output.read_bytes() == b'second resource'


@pytest.mark.parametrize('existing', [b'', b'valuable legacy video'])
def test_unknown_output_and_part_are_preserved_instead_of_adopted(tmp_path, existing):
    old = tmp_path/'video.mp4'; old.write_bytes(existing)
    part = tmp_path/'video.mp4.part'; part.write_bytes(b'old partial')
    store = storage(tmp_path)
    with store.session():
        store.bind_media(dict(playlist='a')); store.reserve_output(candidates(tmp_path))
        assert store.output != old and not store.output_exists()
    assert old.read_bytes() == existing and part.read_bytes() == b'old partial'


def test_completed_same_resource_is_reused_only_while_digest_matches(tmp_path):
    first = storage(tmp_path)
    with first.session():
        first.bind_media(dict(playlist='a')); first.reserve_output(candidates(tmp_path))
        first.output.write_bytes(b'original'); first.publish_output()
    second = storage(tmp_path)
    with second.session():
        second.bind_media(dict(playlist='a')); second.reserve_output(candidates(tmp_path))
        assert second.output == first.output and second.output_exists()
    first.output.write_bytes(b'tampered')
    third = storage(tmp_path)
    with third.session():
        third.bind_media(dict(playlist='a')); third.reserve_output(candidates(tmp_path))
        assert third.output != first.output and not third.output_exists()


def test_signed_url_candidate_identity_preserves_media_selectors():
    a = 'https://cdn.example.test/0.ts?token=secret-a&n=1'
    b = 'https://cdn.example.test/0.ts?token=secret-b&n=1'
    assert url_fingerprint(a, signed=True) == url_fingerprint(b, signed=True)
    assert url_fingerprint(a) != url_fingerprint(b)
    assert url_fingerprint(a, signed=True) != url_fingerprint(b.replace('n=1','n=2'), signed=True)
    assert url_fingerprint(a+'&n=2') != url_fingerprint(a.replace('n=1', 'n=2')+'&n=1')


@pytest.fixture
def hls_runtime(monkeypatch):
    state = dict(token='secret-a', fail_second=False, calls=[], etag='"asset-v1"',
                 payload=b'\x47'+bytes(187), conditional=True)
    def load(self, url):
        return m3u8.loads('#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n0.ts?token='+state['token']+
            '\n#EXTINF:1,\n1.ts?token='+state['token']+'\n#EXT-X-ENDLIST\n', uri=url)
    def get(url, headers, **kwargs):
        state['calls'].append((url, dict(headers)))
        if state['fail_second'] and '/1.ts' in url:
            return SimpleNamespace(status_code=503)
        if state['conditional'] and headers.get('If-None-Match') == state['etag']:
            return SimpleNamespace(status_code=304)
        return SimpleNamespace(status_code=200, content=state['payload'], headers={'ETag':state['etag']})
    monkeypatch.setattr(OfflineCrawler, '_load_m3u8', load)
    monkeypatch.setattr(base, '_http_get', get)
    monkeypatch.setattr(OfflineCrawler, '_remux_to_mp4', lambda self, src, dst, work: shutil.copyfile(src,dst) is not None)
    monkeypatch.setattr(base.config, 'get_filename_template', lambda: '{title}')
    return state


def test_production_hls_preserves_unknown_output_and_reuses_owned_mirror(tmp_path, hls_runtime):
    legacy = tmp_path/'SAME-001 shared title.mp4'; legacy.write_bytes(b'user file')
    first = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(tmp_path), silence=True)
    assert first.start_download() is True
    output = Path(first._get_video_savename())
    assert output != legacy and legacy.read_bytes() == b'user file'
    assert output.read_bytes() == (b'\x47'+bytes(187))*2
    count = len(hls_runtime['calls'])
    mirror = OfflineCrawler('https://fs1.app/videos/same-001/', savepath=str(tmp_path), silence=True)
    assert mirror.start_download() is True
    assert mirror._get_video_savename() == str(output)
    assert len(hls_runtime['calls']) == count + 2
    assert all(h.get('If-None-Match') == '"asset-v1"' for _,h in hls_runtime['calls'][count:])


def test_signed_hls_refresh_revalidates_cache_without_storing_tokens(tmp_path, hls_runtime):
    hls_runtime['fail_second'] = True
    first = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(tmp_path), silence=True)
    first._segment_retry_rounds = 1
    with pytest.raises(base.DownloadIncompleteError):
        first.start_download()
    manifest = Path(first._temp_folder)/'manifest.json'
    assert 'secret-a' not in manifest.read_text()
    hls_runtime['fail_second'] = False
    hls_runtime['token'] = 'secret-b'; hls_runtime['calls'].clear()
    resumed = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(tmp_path), silence=True)
    assert resumed.start_download() is True
    assert resumed._temp_folder == first._temp_folder
    assert any('/0.ts' in u and h.get('If-None-Match') == '"asset-v1"' for u,h in hls_runtime['calls'])
    assert Path(resumed._get_video_savename()).read_bytes() == (b'\x47'+bytes(187))*2
    assert 'secret-b' not in manifest.read_text()


def test_same_crawler_instance_revalidates_partial_cache_on_another_start(tmp_path, hls_runtime):
    job = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(tmp_path), silence=True)
    job._segment_retry_rounds = 1
    hls_runtime['fail_second'] = True
    with pytest.raises(base.DownloadIncompleteError):
        job.start_download()
    hls_runtime.update(fail_second=False, etag='"new-asset"', payload=b'\x47'+b'Z'*187)
    assert job.start_download() is True
    assert Path(job._get_video_savename()).read_bytes() == hls_runtime['payload']*2


def test_real_hls_cancel_defers_cleanup_until_worker_exit(tmp_path, hls_runtime, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(status_code=200, content=b'\x47'+bytes(187), headers={})
    monkeypatch.setattr(base, '_http_get', blocked)
    first = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(tmp_path), silence=True, max_workers=1)
    results, errors = [], []
    def run():
        try:
            results.append(first.start_download())
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    try:
        worker.start(); assert entered.wait(2)
        cache = Path(first._temp_folder)
        first.cancel_download(cleanup=True)
        assert cache.exists()
        rival = storage(tmp_path, resolution='highest')
        with pytest.raises(ResourceBusyError):
            with rival.session():
                pass
        release.set(); worker.join(3)
        assert not worker.is_alive() and not errors and results == [False]
        assert not cache.exists()
        with rival.session():
            pass
    finally:
        release.set(); worker.join(4)


def test_storage_releases_locks_on_error(tmp_path):
    first = storage(tmp_path)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with first.session():
            first.bind_media(dict(playlist='a')); first.reserve_output(candidates(tmp_path))
            raise RuntimeError('synthetic failure')
    rival = storage(tmp_path)
    with rival.session():
        rival.bind_media(dict(playlist='a')); rival.reserve_output(candidates(tmp_path))


def test_production_direct_owns_output_and_checks_signed_refresh_validator(tmp_path, monkeypatch):
    state = dict(token='first-secret', etag='"v1"', chunks=0, payload=b'video!')
    class Direct(supjav.SiteSupJav):
        def get_url_infos(self):
            self._targetName = 'DIRECT-001'
            self._direct_url = 'https://cdn.example.test/video?token='+state['token']
            self._direct_referer = 'https://supjav.com/'
            self._m3u8url = None
        def is_url_vaildate(self):
            return bool(self._direct_url)
    class Response:
        status_code = 200
        headers = {'content-length': '6'}
        def __init__(self, probe):
            self.headers = dict(self.headers, ETag=state['etag'])
            self.probe = probe
        def iter_content(self, chunk_size):
            state['chunks'] += 1
            yield state['payload']
        def close(self):
            pass
    monkeypatch.setattr(supjav, '_get_session', lambda: SimpleNamespace(
        get=lambda url, headers, **kwargs: Response('Range' in headers)))
    monkeypatch.setattr(base.config, 'get_filename_template', lambda: '{title}')
    first = Direct('https://supjav.com/12345.html', savepath=str(tmp_path), silence=True)
    assert first._download_direct() is True
    first_output = first._get_video_savename()
    assert Path(first_output).read_bytes() == b'video!'
    state['token'] = 'second-secret'
    refreshed = Direct('https://supjav.com/12345.html', savepath=str(tmp_path), silence=True)
    assert refreshed._download_direct() is True
    assert refreshed._get_video_savename() == first_output and state['chunks'] == 1
    state['etag'] = '"changed-media"'
    changed = Direct('https://supjav.com/12345.html', savepath=str(tmp_path), silence=True)
    assert changed._download_direct() is True
    assert changed._get_video_savename() != first_output and state['chunks'] == 2
    state['etag'] = ''
    unvalidated = Direct('https://supjav.com/12345.html', savepath=str(tmp_path), silence=True)
    assert unvalidated._download_direct() is True
    unvalidated_path = unvalidated._get_video_savename()
    repeat = Direct('https://supjav.com/12345.html', savepath=str(tmp_path), silence=True)
    assert repeat._download_direct() is True and repeat._get_video_savename() == unvalidated_path
    state['payload'] = b'other!'
    replaced = Direct('https://supjav.com/12345.html', savepath=str(tmp_path), silence=True)
    assert replaced._download_direct() is True and replaced._get_video_savename() != unvalidated_path
    assert Path(unvalidated_path).read_bytes() == b'video!'
    assert Path(replaced._get_video_savename()).read_bytes() == b'other!'
    for manifest in (tmp_path/'.uav').rglob('*.json'):
        assert 'first-secret' not in manifest.read_text() and 'second-secret' not in manifest.read_text()


def test_same_hls_urls_with_changed_bytes_preserve_previous_output(tmp_path, hls_runtime):
    url = 'https://jable.tv/videos/same-001/'
    old = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    assert old.start_download() is True
    original_path = Path(old._get_video_savename())
    original = original_path.read_bytes()
    hls_runtime.update(etag='"asset-v2"', payload=b'\x47'+b'B'*187)
    new = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    assert new.start_download() is True
    assert new._get_video_savename() != str(original_path)
    assert original_path.read_bytes() == original
    assert Path(new._get_video_savename()).read_bytes() == hls_runtime['payload']*2
    again = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    assert again.start_download() is True
    assert again._get_video_savename() == new._get_video_savename()


def test_signature_refresh_without_etag_requires_matching_bytes(tmp_path, hls_runtime):
    hls_runtime.update(etag='', conditional=False)
    url = 'https://jable.tv/videos/same-001/'
    first = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    assert first.start_download() is True
    hls_runtime['token'] = 'new-secret'
    second = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    assert second.start_download() is True
    assert second._get_video_savename() == first._get_video_savename()
    assert len(hls_runtime['calls']) == 4  # Two actual bodies verify the old output.


def test_completed_provenance_survives_later_cleanup(tmp_path, hls_runtime):
    job = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(tmp_path), silence=True)
    assert job.start_download() is True
    manifest = Path(job._temp_folder)/'manifest.json'
    assert job.cleanup_temp() is False
    assert manifest.exists() and job.is_target_video_exist()


def test_cannot_verify_source_does_not_report_previous_output_as_new_success(tmp_path, hls_runtime):
    url = 'https://jable.tv/videos/same-001/'
    first = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    assert first.start_download() is True
    path = Path(first._get_video_savename())
    original = path.read_bytes()
    hls_runtime['fail_second'] = True
    second = OfflineCrawler(url, savepath=str(tmp_path), silence=True)
    with pytest.raises(ConnectionError, match='Cannot verify'):
        second.start_download()
    assert path.read_bytes() == original


def test_corrupt_entry_metadata_cannot_make_cache_or_output_valid(tmp_path):
    store = storage(tmp_path)
    with store.session():
        store.bind_media(dict(playlist='a'))
        segment = write_segment(store)
        store.manifest['segments']['0']['bytes'] = 'not a size'
        assert not store.local_segment_valid(0)
        assert segment.exists()
        store.reserve_output(candidates(tmp_path))
        store.output.write_bytes(b'original'); store.publish_output()
        meta = json.loads(store.output_record.read_text())
        meta['bytes'] = []
        store.output_record.write_text(json.dumps(meta))
        assert not store.output_exists()


def test_shortened_directory_hash_collision_still_has_one_owner(tmp_path, monkeypatch):
    real_hash = download_storage.identity_hash
    def collision(value):
        original = real_hash(value)
        return 'a'*16 + original[16:]
    monkeypatch.setattr(download_storage, 'identity_hash', collision)
    a,b = storage(tmp_path,'a-001'),storage(tmp_path,'b-001')
    assert a.task_key != b.task_key and a.task_root == b.task_root
    with a.session():
        a.bind_media(dict(playlist='same'))
        old = write_segment(a)
        with pytest.raises(ResourceBusyError):
            with b.session():
                pass
    with b.session():
        b.bind_media(dict(playlist='same'))
        assert b.cache != a.cache
        assert not b.reusable_segment(0,'https://cdn.example.test/0.ts')
    assert old.read_bytes() == b'controlled segment'


def test_failed_manifest_replace_preserves_previous_recoverable_metadata(tmp_path, monkeypatch):
    store = storage(tmp_path)
    with store.session():
        store.bind_media(dict(playlist='a'))
        write_segment(store)
        path = store.cache/'manifest.json'
        before = path.read_bytes()
        def failed_replace(*args, **kwargs):
            raise OSError(errno.EIO, 'synthetic unavailable device')
        monkeypatch.setattr(download_storage.os, 'replace', failed_replace)
        with pytest.raises(StorageWriteError):
            store.record_segment(1, 'https://example.test/1.ts', b'new segment')
        assert path.read_bytes() == before
        assert not list(store.cache.glob('*.tmp'))


def test_incomplete_owned_reservation_cannot_adopt_a_leftover_file(tmp_path):
    first = storage(tmp_path)
    with first.session():
        first.bind_media(dict(playlist='a')); first.reserve_output(candidates(tmp_path))
        first.output.write_bytes(b'not published')
    second = storage(tmp_path)
    with second.session():
        second.bind_media(dict(playlist='a')); second.reserve_output(candidates(tmp_path))
        assert second.output != first.output and not second.output_exists()
    assert first.output.read_bytes() == b'not published'


@pytest.mark.parametrize('parallel', [False, True])
def test_direct_validator_changes_stop_transfer_without_retry_or_fallback(tmp_path, monkeypatch, parallel):
    calls = []
    total = 262144
    class Response:
        def __init__(self, start=None, end=None):
            self.status_code = 200 if start is None else 206
            self.headers = {'etag':'"v2"', 'content-length':str(total)}
            if start is not None:
                self.headers['content-range'] = f'bytes {start}-{end}/{total}'
        def iter_content(self, chunk_size):
            pytest.fail('changed resource must be rejected before reading chunks')
        def close(self):
            pass
    def get(url, headers, **kwargs):
        calls.append(dict(headers))
        if headers.get('Range') == 'bytes=0-0':
            return Response(0, 0)
        assert headers.get('If-Match') == '"v1"'
        if 'Range' in headers:
            start, end = map(int, headers['Range'].removeprefix('bytes=').split('-'))
            return Response(start, end)
        return Response()
    monkeypatch.setattr(supjav, '_get_session', lambda: SimpleNamespace(get=get))
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._direct_expected_etag = '"v1"'; job._direct_url = 'https://cdn.example.test/video'
    job._cancel_job = False; job._max_workers = 2
    method = job._download_direct_ranges if parallel else job._download_direct_serial
    with pytest.raises(ResourceChangedError):
        method(str(tmp_path/'verify.part'), '', 0)
    assert len(calls) <= (3 if parallel else 1)


def test_actual_storage_ownership_coordinates_processes_for_same_and_different_media(tmp_path):
    script = """from uav_downloader.core.download_storage import DownloadStorage, task_identity
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=DownloadStorage(p,task_identity('https://jable.tv/videos/same-001/','same-001','720'))
with s.session():
    s.bind_media({'playlist':'a'})
    s.reserve_output(lambda attempt,owner:p/'video.mp4')
    s.output.write_bytes(b'child-owned resource')
    s.publish_output()
    print('owned',flush=True)
    sys.stdin.readline()
"""
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
    process = subprocess.Popen([sys.executable,'-u','-c',script,str(tmp_path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        assert process.stdout.readline().strip() == 'owned'
        mirror = DownloadStorage(tmp_path, task_identity('https://fs1.app/videos/same-001/','same-001','720'))
        with pytest.raises(ResourceBusyError):
            with mirror.session():
                pass
        other = storage(tmp_path, 'another-002')
        with other.session():
            other.bind_media(dict(playlist='b')); other.reserve_output(candidates(tmp_path))
            assert other.output != tmp_path/'video.mp4'
            other.output.write_bytes(b'parent resource'); other.publish_output()
        assert (tmp_path/'video.mp4').read_bytes() == b'child-owned resource'
        process.stdin.write('\n'); process.stdin.flush(); process.communicate(timeout=5)
        with mirror.session():
            mirror.bind_media(dict(playlist='a')); mirror.reserve_output(candidates(tmp_path))
            assert mirror.output == tmp_path/'video.mp4' and mirror.output_exists()
    finally:
        if process.poll() is None:
            process.kill(); process.communicate(timeout=5)


def test_real_output_collision_remains_portable_with_unicode_and_long_title(tmp_path, hls_runtime, monkeypatch):
    destination = tmp_path / ('保存位置_' + 'x'*38)
    destination.mkdir()
    def infos(self):
        self._targetName = '影片😀'*85
        self._m3u8url = 'https://cdn.example.test/master.m3u8'
    monkeypatch.setattr(OfflineCrawler,'get_url_infos',infos)
    a = OfflineCrawler('https://jable.tv/videos/same-001/', savepath=str(destination), silence=True)
    b = OfflineCrawler('https://missav.ai/same-001', savepath=str(destination), silence=True)
    assert a.start_download() is True and b.start_download() is True
    assert a._get_video_savename() != b._get_video_savename()
    for job in (a,b):
        path = Path(job._get_video_savename())
        assert len((str(path)+'.part').encode('utf-16-le'))//2 <= 255
        assert len((path.name+'.part').encode('utf-8')) <= 255
        assert path.is_file()
