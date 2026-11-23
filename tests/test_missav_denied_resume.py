"""The #70 rejection profile, served by an offline fake transport."""
import pytest
from uav_downloader.sites import base
from uav_downloader.sites.missav import SiteMissAV
from test_hls_segment_recovery import _job, PACKET


@pytest.mark.parametrize('workers', [1, 4])
def test_large_denial_preserves_bandwidth_and_retry_progress(tmp_path, workers):
    total, cached = 231, 108
    manifest = '#EXTM3U\n' + ''.join(f'#EXTINF:1,\ns{i}\n' for i in range(total))
    resources = {f's{i}': (403, b'') for i in range(cached, total)}
    for i in range(cached):
        (tmp_path / f'{i:06d}.mp4').write_bytes(PACKET)
    job, calls = _job(tmp_path, manifest, resources)
    job.__class__ = SiteMissAV
    job._max_workers = workers
    progress = []
    job._progress_callback = lambda done, count, _: progress.append((done, count))
    with pytest.raises(base.DownloadIncompleteError) as failed:
        job.start_download()
    assert calls == ['playlist', 's108']
    assert failed.value.remaining_segments == 123
    assert 'HTTP 403 ×1' in str(failed.value) and '122 not requested' in str(failed.value)
    assert progress == [(cached, total)]
    resources.update({k: (200, PACKET) for k in resources})
    calls.clear()
    assert job.start_download()
    assert sorted(calls) == sorted(['playlist', *resources])
    assert progress[-1] == (total, total)
    assert all(p[0] >= cached and p[1] == total for p in progress)


def test_first_attempt_widespread_denial_does_not_sweep_playlist(tmp_path):
    manifest = '#EXTM3U\n' + ''.join(f'#EXTINF:1,\ns{i}\n' for i in range(123))
    job, calls = _job(tmp_path, manifest, {f's{i}': (403, b'') for i in range(123)})
    job.__class__ = SiteMissAV
    job._max_workers = 1
    with pytest.raises(base.DownloadIncompleteError) as failed:
        job.start_download()
    assert calls == ['playlist', 's0', 's1', 's2']
    assert 'HTTP 403 ×3' in str(failed.value) and '120 not requested' in str(failed.value)
