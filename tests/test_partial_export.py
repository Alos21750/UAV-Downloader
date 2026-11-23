"""Only media bytes positively identified by a failed download may be exported."""
import os
import subprocess
from pathlib import Path

import pytest

from uav_downloader.core.partial_export import Receipts, export_prefix


def archive(tmp_path, chunks):
    cache = tmp_path / 'cache'
    cache.mkdir()
    receipts = Receipts()
    for index, payload in chunks.items():
        (cache / f'{index:06d}.mp4').write_bytes(payload)
        receipts.record(index, payload)
    return receipts, cache


def test_plan_only_contains_verified_contiguous_prefix(tmp_path):
    receipts, cache = archive(tmp_path, {0: b'one', 1: b'two', 3: b'four'})
    (cache / '000002.mp4').write_bytes(b'unowned older attempt')
    plan = receipts.plan(cache, tmp_path / 'movie.mp4', 4)
    assert len(plan.hashes) == 2 and plan.total == 4
    assert receipts.plan(cache, tmp_path / 'movie.mp4', 2) is None
    assert Receipts().plan(cache, tmp_path / 'movie.mp4', 4) is None


@pytest.mark.parametrize('change', ['mutated', 'missing', 'cancel'])
def test_change_or_cancel_never_publishes_or_mutates_cache(tmp_path, change):
    receipts, cache = archive(tmp_path, {0: b'one', 1: b'two'})
    plan = receipts.plan(cache, tmp_path / 'movie.mp4', 3)
    segment = cache / '000001.mp4'
    if change == 'mutated':
        segment.write_bytes(b'new')
    elif change == 'missing':
        segment.unlink()
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    with pytest.raises((ValueError, OSError, InterruptedError)):
        export_prefix(plan, 'unused-ffmpeg', lambda: change == 'cancel')
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    assert list(tmp_path.iterdir()) == [cache]


def test_real_mpegts_to_distinct_no_clobber_mp4(tmp_path):
    encoder = Path(pytest.importorskip('imageio_ffmpeg').get_ffmpeg_exe())
    bundle = os.environ.get('UAV_FFMPEG_BUNDLE')
    decoder = Path(bundle) / 'ffmpeg-win-x86_64-v7.1.exe' if bundle else encoder
    ts = tmp_path / 'test.ts'
    subprocess.run([encoder, '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=32x32:r=10', '-t', '1', '-c:v', 'libx264', '-threads', '1',
                    '-f', 'mpegts', str(ts)], check=True, timeout=30)
    source = ts.read_bytes()
    receipts, cache = archive(tmp_path, {0: source, 2: source})
    movie = tmp_path / 'movie.mp4'
    movie.write_bytes(b'original')
    (tmp_path / 'movie.partial.mp4').write_bytes(b'user clip')
    result = export_prefix(receipts.plan(cache, movie, 3), decoder)
    assert result.name == 'movie.partial-2.mp4'
    assert result.read_bytes()[4:8] == b'ftyp'
    subprocess.run([encoder, '-v', 'error', '-xerror', '-i', str(result), '-f',
                    'null', '-'], check=True, timeout=30)
    assert movie.read_bytes() == b'original'
    assert (tmp_path / 'movie.partial.mp4').read_bytes() == b'user clip'
    assert (cache / '000000.mp4').read_bytes() == source


def test_browser_keeps_recovery_receipt_but_never_finishes_partial(tmp_path, monkeypatch):
    from uav_downloader.apps import browse
    from uav_downloader.sites.base import DownloadIncompleteError
    from uav_downloader.sites.missav import SiteMissAV

    job = SiteMissAV.__new__(SiteMissAV)
    job._cancel_job = False
    job._temp_folder = str(tmp_path / 'cache')
    job._tsList = ['seg0', 'seg1']
    monkeypatch.setattr(job, 'is_url_vaildate', lambda: True)
    monkeypatch.setattr(job, 'target_name', lambda: 'fixture')
    monkeypatch.setattr(job, 'add_source_subtitle_evidence', lambda *_: None)
    monkeypatch.setattr(job, '_get_video_savename', lambda: str(tmp_path / 'fixture.mp4'))
    def missing():
        job._partial_segment_written(0, b'first')
        raise DownloadIncompleteError(1)
    monkeypatch.setattr(job, 'start_download', missing)
    monkeypatch.setattr(browse.M3U8Sites, 'CreateSite', lambda *a, **kw: job)
    manager = browse.DownloadManager(auto_retry_count_getter=lambda: 0)
    item = manager.add_item('https://missav.ws/test-123', dest=str(tmp_path))
    manager._run(item.url, str(tmp_path))
    assert item.state == '未完成'
    assert item.partial_prefix.total == 2 and len(item.partial_prefix.hashes) == 1
