"""Exercises public refusal and timing semantics without contacting a provider."""
from types import SimpleNamespace

import pytest

from uav_downloader.apps import browse, watch
from uav_downloader.apps.downloads import classify_download_problem
from uav_downloader.i18n.locales import STRINGS
from uav_downloader.sites import base, missav


@pytest.mark.parametrize('status', [429, 503])
def test_retry_after_covers_next_job_without_delaying_other_origins(monkeypatch, status):
    monkeypatch.setattr(missav.SiteMissAV, '_media_cooldowns', {})
    clock, calls = [90.0], []
    monkeypatch.setattr(missav.time, 'monotonic', lambda: clock[0])
    def fetch(_self, url, **kw):
        calls.append(url)
        return SimpleNamespace(status_code=status if len(calls) == 1 else 200,
                               headers={'Retry-After': '12'} if len(calls) == 1 else {})
    monkeypatch.setattr(base.M3U8Crawler, '_media_get', fetch)
    a, b = (object.__new__(missav.SiteMissAV) for _ in range(2))
    url = 'https://shared.example:443/first'
    assert a._media_get(url).status_code == status
    with pytest.raises(missav._MediaCooldown):
        b._media_get('https://shared.example/next')
    assert b._media_get('https://independent.example/next').status_code == 200
    clock[0] += 12
    assert b._media_get('https://shared.example/next').status_code == 200
    assert calls == [url, 'https://independent.example/next', 'https://shared.example/next']


def test_denied_hint_survives_old_queue_and_transients_keep_retry(tmp_path):
    error = base.DownloadIncompleteError(2, detail='HTTP 403 ×1; access denied; cache retained; retry manually later')
    m = browse.DownloadManager()
    row = m.add_item('https://example.invalid/qa', state='未完成')
    row.error, row.error_code = str(error), 'incomplete_segments'  # v3.8.3 CSV
    filename = str(tmp_path / 'queue.csv')
    m.save_csv(filename)
    restored = browse.DownloadManager()
    restored.load_csv(filename)
    assert restored.get_items()[0].error_code == 'segment_access_denied'
    p = classify_download_problem(error, '未完成')
    assert p.code == 'segment_access_denied' and not p.retryable
    assert all(d.get('problem_segment_access_denied') for d in STRINGS.values())
    transient = classify_download_problem(base.DownloadIncompleteError(1, detail='HTTP 503 ×1'))
    assert transient.code == 'incomplete_segments' and transient.retryable


@pytest.mark.parametrize('denied', [True, False])
def test_watcher_does_not_advertise_repeated_retries_for_denials(denied, monkeypatch, tmp_path):
    error = base.DownloadIncompleteError(1, detail=(
        'HTTP 403 ×1; access denied; cache retained; retry manually later'
        if denied else 'HTTP 503 ×1'))
    def fail():
        raise error
    job = SimpleNamespace(is_url_vaildate=lambda: True, _cancel_job=False, start_download=fail)
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *a, **kw: job)
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    logs = []
    worker = watch.SmallToolWorker(logs.append)
    result = worker._download_one({'url':'https://example.invalid/qa','title':'QA'}, str(tmp_path))
    assert result == 'download_incomplete' and not worker._seen
    assert any('[HTTP 403]' in line for line in logs) is denied
    assert any('[RETRY]' in line for line in logs) is not denied
