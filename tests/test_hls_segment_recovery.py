"""Offline segment-level recovery regressions; no real pages, keys or media."""
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from uav_downloader.sites import base

PACKET = bytes([0x47]) + bytes(187)
SECRET = 'do-not-log-this'


def _job(tmp_path, body, resources):
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._temp_folder = str(tmp_path)
    job._dest_folder, job._dirName, job._targetName = str(tmp_path), 'fixture', 'fixture'
    job._speed_lock = threading.Lock()
    job._segment_retry_rounds = 1
    job._segment_retry_base_delay = 0
    job._segment_retry_max_delay = 0
    job._max_workers = 2
    job._progress_callback = None
    job._m3u8url = 'https://example.invalid/playlist'
    job._create_dest_folder = job.download_image = lambda: None
    job.is_target_video_exist = lambda: False
    job._mergeMp4Chunks = lambda: True
    calls = []

    def get(url, **kwargs):
        key = url.rsplit('/', 1)[-1].split('?')[0]
        calls.append(key)
        if key == 'playlist':
            return SimpleNamespace(status_code=200, text=body, url=url)
        item = resources[key]
        if isinstance(item, Exception):
            raise item
        status, data = item
        return SimpleNamespace(status_code=status, content=data, close=lambda: None)
    job._media_get = get
    return job, calls


def _seed_cache(job, calls):
    """Only a cache bound to this very playlist is eligible for resume."""
    job._create_m3u8()
    cache = Path(job._dest_folder) / base.hls_cache.cache_name(
        job._dirName, job._targetName, job._playlist_fingerprint)
    cache.mkdir(exist_ok=True)
    calls.clear()
    return cache


def test_rotating_keys_ivs_and_clear_segments(tmp_path):
    keys = [b'A' * 16, b'B' * 16]
    manifest = ('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:20\n#EXTINF:1,\nclear0\n'
                '#EXT-X-KEY:METHOD=AES-128,URI="keyA"\n#EXTINF:1,\na\n'
                '#EXT-X-KEY:METHOD=AES-128,URI="keyB",IV=0xA\n#EXTINF:1,\nb\n'
                '#EXT-X-KEY:METHOD=AES-128,URI="keyB",IV=0xB\n#EXTINF:1,\nc\n'
                '#EXT-X-KEY:METHOD=NONE\n#EXTINF:1,\nclear1\n#EXT-X-ENDLIST\n')
    def encrypt(key, iv):
        return AES.new(key, AES.MODE_CBC, iv.to_bytes(16, 'big')).encrypt(pad(PACKET, 16))
    resources = dict(keyA=(200, keys[0]), keyB=(200, keys[1]), clear0=(200, PACKET),
                     clear1=(200, PACKET), a=(200, encrypt(keys[0], 21)),
                     b=(200, encrypt(keys[1], 10)), c=(200, encrypt(keys[1], 11)))
    job, calls = _job(tmp_path, manifest, resources)
    assert job.start_download() is True
    assert calls.count('keyB') == 1
    assert all(Path(job._seg_savename(i)).read_bytes() == PACKET for i in range(5))


def test_retry_details_are_bounded_and_redacted(tmp_path):
    manifest = '#EXTM3U\n' + ''.join(f'#EXTINF:1,\n{i}?token={SECRET}\n' for i in range(24))
    resources = {str(i): [(429, b''), (200, b''), (200, b'not media'),
                           ConnectionError(SECRET)][i % 4] for i in range(24)}
    job, _ = _job(tmp_path, manifest, resources)
    with pytest.raises(base.DownloadIncompleteError) as caught:
        job.start_download()
    msg = str(caught.value)
    for term in ('HTTP 429', 'empty', 'invalid', 'transport'):
        assert term in msg
    assert SECRET not in msg and 'https:' not in msg and len(msg) < 500
    assert caught.value.remaining_segments == 24


def test_cancel_after_permit_never_publishes_segment(monkeypatch, tmp_path):
    job, _ = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n', {'a': (200, PACKET)})
    def cancel(*args, **kwargs):
        job._cancel_job = True
        return True
    monkeypatch.setattr(base.speed_limiter, 'acquire', cancel)
    assert not job.start_download()
    assert job._pending_set and not list(Path(job._temp_folder).iterdir())


def test_resume_replaces_bad_cache_only_after_valid_transfer(tmp_path):
    job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n#EXTINF:1,\nb\n', {
        'a': (200, b'bad network body'), 'b': (200, PACKET)})
    cache = _seed_cache(job, calls)
    damaged = cache / '000000.mp4'
    damaged.write_bytes(b'not a saved packet')
    retained = cache / '000001.mp4'
    retained.write_bytes(PACKET)
    with pytest.raises(base.DownloadIncompleteError):
        job.start_download()
    assert damaged.read_bytes() == b'not a saved packet'
    assert retained.read_bytes() == PACKET and calls.count('b') == 0
    job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n#EXTINF:1,\nb\n', {
        'a': (200, PACKET)})
    assert job.start_download()
    assert calls == ['playlist', 'a'] and damaged.read_bytes() == PACKET


def test_suppressed_retry_shows_http_status_without_exception_text(tmp_path):
    class Suppressed(Exception):
        status_code = 429
    job, _ = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n', {'a': Suppressed(SECRET)})
    with pytest.raises(base.DownloadIncompleteError, match='HTTP 429') as error:
        job.start_download()
    assert SECRET not in str(error.value)


@pytest.mark.parametrize('detail', ['HTTP 404 ×1 (e.g. segment 8)',
                                     'invalid media ×1 (e.g. segment 404)'])
@pytest.mark.parametrize('persisted', [False, True])
def test_partial_segment_404_is_not_a_deleted_video(detail, persisted):
    from uav_downloader.apps.downloads import classify_download_problem
    from uav_downloader.i18n.locales import STRINGS
    err = base.DownloadIncompleteError(1, detail=detail)
    problem = classify_download_problem(str(err) if persisted else err, '未完成')
    assert problem.retryable and problem.code == 'incomplete_segments'
    assert all(locale.get('problem_incomplete_segments') for locale in STRINGS.values())
    assert not classify_download_problem('HTTP 404 video not found', '未完成').retryable


@pytest.mark.parametrize('declaration', [
    'METHOD=SAMPLE-AES,URI="private"', 'METHOD=AES-128,KEYFORMAT="private",URI="private"',
    'METHOD=AES-128,URI="private",IV=0xGG'])
def test_unsupported_key_not_requested(tmp_path, declaration):
    job, calls = _job(tmp_path, f'#EXTM3U\n#EXT-X-KEY:{declaration}\n#EXTINF:1,\na\n', {})
    with pytest.raises(ValueError):
        job.start_download()
    assert calls == ['playlist']


def test_resume_never_follows_a_nonregular_cache_target(tmp_path):
    import os
    if not hasattr(os, 'symlink'):
        pytest.skip('symlinks unavailable')
    unrelated = tmp_path / 'unrelated.ts'
    unrelated.write_bytes(PACKET)
    job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n', {})
    link = _seed_cache(job, calls) / '000000.mp4'
    try:
        link.symlink_to(unrelated)
    except OSError:
        pytest.skip('no symlink privilege')
    with pytest.raises(base.StorageWriteError):
        job.start_download()
    assert link.is_symlink() and unrelated.read_bytes() == PACKET and calls == ['playlist']
