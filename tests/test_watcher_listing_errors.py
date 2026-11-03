"""A listing error is an incomplete scan, never a successful empty page."""
from datetime import datetime, timezone

import pytest

from uav_downloader.apps import watch
from uav_downloader.apps.watch_categories import find_target


@pytest.fixture
def scan_env(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    logs, downloaded = [], []
    worker = watch.SmallToolWorker(logs.append)
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 10, 10, tzinfo=timezone.utc), '2026-10-10')
    worker._download_one = lambda video, dest: downloaded.append((video, dest))
    cfg = {'output_folder': str(tmp_path), 'baseline_date': '2026-01-01',
           'first_run_done': False, 'version_preference': 'standard',
           'selected_targets': [{'site': 'SupJav', 'id': find_target(
               'SupJav', legacy_name='最近更新')['id']}]}
    return worker, cfg, logs, downloaded


def _error(_url):
    raise RuntimeError('Unrecognized SupJav listing')


def test_fetch_wrapper_preserves_listing_error(scan_env, monkeypatch):
    worker, _cfg, _logs, _downloaded = scan_env
    monkeypatch.setattr(watch.SupJavBrowser, 'fetch_page', _error)
    with pytest.raises(RuntimeError, match='Unrecognized'):
        worker._fetch_page_for_site('SupJav', 'https://supjav.com/')


def test_unknown_listing_does_not_mark_first_run_complete(scan_env, monkeypatch):
    worker, cfg, logs, downloaded = scan_env
    monkeypatch.setattr(watch.SupJavBrowser, 'fetch_page', _error)
    assert worker._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is False
    assert downloaded == []
    assert any('[ERR]' in line and 'SupJav' in line for line in logs)
    assert not any('no videos — end' in line for line in logs)
    assert worker._scan_lock.acquire(blocking=False)
    worker._scan_lock.release()


def test_genuine_empty_listing_still_completes_scan(scan_env, monkeypatch):
    worker, cfg, _logs, downloaded = scan_env
    monkeypatch.setattr(watch.SupJavBrowser, 'fetch_page', lambda _url: [])
    assert worker._scan_and_download(cfg) is True
    assert cfg['first_run_done'] is True
    assert downloaded == []


@pytest.mark.parametrize('successful_site_has_video,deduped', [
    (False, False), (True, False), (True, True),
])
def test_failed_site_does_not_prevent_other_site_but_scan_stays_incomplete(
        scan_env, monkeypatch, successful_site_has_video, deduped):
    worker, cfg, logs, downloaded = scan_env
    monkeypatch.setattr(watch.SupJavBrowser, 'fetch_page', _error)
    fetched = []

    def missav(url):
        fetched.append(url)
        if 'page=2' in url or not successful_site_has_video:
            return []
        return [{'url': 'https://missav.ws/test-001', 'title': 'TEST-001'}]

    monkeypatch.setattr(watch.MissAVBrowser, 'fetch_page', missav)
    if deduped:
        monkeypatch.setattr(watch, 'dedupe_video_candidates', lambda *_args: ([], []))
    cfg['selected_targets'].append({'site': 'MissAV', 'id': find_target(
        'MissAV', legacy_name='最近更新')['id']})
    assert worker._scan_and_download(cfg) is False
    assert fetched
    assert len(downloaded) == int(successful_site_has_video and not deduped)
    assert cfg['first_run_done'] is False
    assert any('Scan incomplete' in line for line in logs)


def test_later_listing_failure_preserves_candidates_but_not_scan_success(scan_env, monkeypatch):
    worker, cfg, logs, downloaded = scan_env
    fetched = []

    def fetch(url):
        fetched.append(url)
        if len(fetched) > 1:
            raise RuntimeError('Later page temporarily unavailable')
        return [{'url': 'https://missav.ws/test-001', 'title': 'TEST-001'}]

    monkeypatch.setattr(watch.MissAVBrowser, 'fetch_page', fetch)
    cfg['selected_targets'] = [{'site': 'MissAV', 'id': find_target(
        'MissAV', legacy_name='最近更新')['id']}]
    assert worker._scan_and_download(cfg) is False
    assert len(fetched) == 2
    assert len(downloaded) == 1
    assert cfg['first_run_done'] is False
    assert any('Later page temporarily unavailable' in line for line in logs)


def test_existing_completed_first_run_flag_is_not_reset_by_listing_failure(scan_env, monkeypatch):
    worker, cfg, _logs, _downloaded = scan_env
    cfg['first_run_done'] = True
    monkeypatch.setattr(watch.SupJavBrowser, 'fetch_page', _error)
    assert worker._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is True


def test_real_cf_block_retains_blocked_diagnostic(scan_env, monkeypatch):
    worker, cfg, logs, _downloaded = scan_env

    def blocked(url):
        raise watch.MirrorsBlockedError(url)

    monkeypatch.setattr(watch.SupJavBrowser, 'fetch_page', blocked)
    assert worker._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is False
    assert any('[BLOCKED]' in line for line in logs)
