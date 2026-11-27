"""Release gate: failure -> manual retry -> failure -> restart -> verified export."""
import subprocess
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from uav_downloader.apps import browse
from uav_downloader.apps.downloads import classify_download_problem
from uav_downloader.core.partial_export import export_prefix
from uav_downloader.sites import base
from uav_downloader.sites.missav import SiteMissAV
from test_hls_segment_recovery import _job, PACKET


def test_denied_segment_is_not_requested_ten_times(tmp_path):
    media = {'ok': (200, PACKET), 'missing': (403, b'')}
    job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\nok\n#EXTINF:1,\nmissing\n', media)
    job.__class__ = SiteMissAV
    job._segment_retry_rounds = 10
    with pytest.raises(base.DownloadIncompleteError) as result:
        job.start_download()
    assert calls.count('missing') == 1
    assert not classify_download_problem(str(result.value)).retryable
    assert '403' in str(result.value) and Path(job._seg_savename(0)).read_bytes() == PACKET
    media['missing'] = (200, PACKET)
    assert job.start_download()
    assert calls.count('ok') == 1 and calls.count('missing') == 2


def test_retry_parse_failure_restart_and_real_export(tmp_path, monkeypatch):
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    original = tmp_path / 'fixture.ts'
    subprocess.run([ff, '-v','error','-f','lavfi','-i','color=s=32x32:r=10',
                    '-t','1','-c:v','libx264','-threads','1','-f','mpegts',str(original)],
                   check=True, timeout=30)
    cache = tmp_path / 'test-123'
    cache.mkdir()
    target = tmp_path / 'movie.mp4'
    data = original.read_bytes()
    job = SiteMissAV.__new__(SiteMissAV)
    job._cancel_job = False
    job._temp_folder, job._tsList = str(cache), ['seg0', 'seg1']
    job.is_url_vaildate = lambda: True
    job.target_name = lambda: 'movie'
    job.add_source_subtitle_evidence = lambda *_: None
    job._get_video_savename = lambda: str(target)
    def fail():
        (cache / '000000.mp4').write_bytes(data)
        job._partial_segment_written(0, data)
        raise base.DownloadIncompleteError(1)
    job.start_download = fail
    monkeypatch.setattr(browse.M3U8Sites,'CreateSite',lambda *a, **kw: job)
    manager = browse.DownloadManager(auto_retry_count_getter=lambda: 0)
    url = 'https://missav.ws/test-123'
    item = manager.add_item(url, dest=str(tmp_path))
    manager._run(url, str(tmp_path))
    assert item.partial_prefix
    job.is_url_vaildate = lambda: False
    monkeypatch.setattr(manager, '_start_download_thread', manager._run_download)
    browse.ModernApp._retry_download(SimpleNamespace(_dlmgr=manager), url)
    assert item.state == '封鎖/解析失敗'
    csv = str(tmp_path / 'queue.csv')
    manager.save_csv(csv)
    restored = browse.DownloadManager()
    restored.load_csv(csv)
    row, = restored.get_items()
    plan = row.partial_prefix
    assert plan and plan.total == 2 and len(plan.hashes) == 1
    assert browse.available_prefix(row) is plan
    # A different interpreter, not a second object with the first process's memory.
    bundle = os.environ.get('UAV_FFMPEG_BUNDLE')
    remuxer = str(Path(bundle) / 'ffmpeg-win-x86_64-v7.1.exe') if bundle else ff
    check = """import sys
from uav_downloader.apps.browse import DownloadManager, available_prefix
from uav_downloader.core.partial_export import export_prefix
m = DownloadManager()
m.load_csv(sys.argv[1])
row, = m.get_items()
assert available_prefix(row) and len(row.partial_prefix.hashes) == 1
export_prefix(row.partial_prefix, sys.argv[2])
"""
    subprocess.run([sys.executable, '-c', check, csv, remuxer], check=True, timeout=25,
                   env={**os.environ, 'PYTHONPATH': str(Path(browse.__file__).resolve().parents[2])})
    clip = tmp_path / 'movie.partial.mp4'
    subprocess.run([ff, '-v','error','-xerror','-i',str(clip),'-f','null','-'],
                   check=True, timeout=30)
    assert not target.exists() and row.state == item.state
    (cache / '000000.mp4').write_bytes(b'X' * len(data))
    with pytest.raises(ValueError, match='changed'):
        export_prefix(plan, ff)
    assert list(tmp_path.glob('*.partial*.mp4')) == [clip]


@pytest.mark.parametrize('modern', [False, True])
def test_receipt_bound_to_settings_job_and_digest(tmp_path, modern):
    from uav_downloader.core.partial_export import Receipts, store_prefix, restore_prefix
    queue = tmp_path / 'queue.csv'
    row = browse.DownloadItem('https://missav.ws/test-123', dest=str(tmp_path))
    receipts = Receipts()
    receipts.record(0, b'original')
    plan = receipts.plan(tmp_path / 'test-123', tmp_path / 'safe.mp4', 3)
    if modern:
        from dataclasses import replace
        from uav_downloader.core.hls_cache import cache_name
        plan = replace(plan, cache=tmp_path / cache_name('test-123', 'safe', 'offline'))
    key = store_prefix(queue, row, plan)
    def restore():
        return restore_prefix(queue, row, key, 'test-123')
    assert restore() == plan
    saved = row.job_id
    row.job_id = 'different-job'
    assert restore() is None
    row.job_id = saved
    assert restore_prefix(queue, row, key, 'another-999') is None
    assert restore_prefix(queue, row, '../outside', 'test-123') is None
    (tmp_path / 'queue.csv.receipts' / key).write_bytes(b'tampered')
    assert restore() is None


def test_transient_segments_still_retry_alongside_denial(tmp_path):
    media = {'a': (403, b''), 'b': (503, b'')}
    job, calls = _job(tmp_path, '#EXTM3U\n#EXTINF:1,\na\n#EXTINF:1,\nb\n', media)
    job.__class__ = SiteMissAV
    get = job._media_get
    def recover(url, **kw):
        if url.endswith('/b') and 'b' in calls:
            media['b'] = (200, PACKET)
        return get(url, **kw)
    job._media_get = recover
    job._segment_retry_rounds = 5
    with pytest.raises(base.DownloadIncompleteError, match='HTTP 403') as err:
        job.start_download()
    assert err.value.remaining_segments == 1
    assert calls.count('a') == 1 and calls.count('b') == 2


@pytest.mark.parametrize('failure', [base.MirrorsBlockedError('denied'), RuntimeError('offline')])
def test_watcher_later_source_failure_preserves_existing_cache(tmp_path, monkeypatch, failure):
    from uav_downloader.apps import watch
    cache = tmp_path / 'test-123'
    cache.mkdir()
    segment = cache / '000000.mp4'
    segment.write_bytes(PACKET)
    job = SiteMissAV.__new__(SiteMissAV)
    job._cancel_job = False
    job._temp_folder = str(cache)
    job.is_target_video_exist = lambda: False
    job.add_source_video_metadata = lambda *_: None
    job.is_url_vaildate = lambda: True
    def fail():
        raise failure
    job.start_download = fail
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *a, **kw: job)
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    log = []
    worker = watch.SmallToolWorker(log.append, lambda *_: None)
    worker._download_one({'url': 'https://missav.ws/test-123', '_site': 'MissAV'}, str(tmp_path))
    assert not worker._seen and segment.read_bytes() == PACKET
    assert any('offline' in line or 'denied' in line for line in log)
