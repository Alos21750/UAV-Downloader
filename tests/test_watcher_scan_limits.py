from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from uav_downloader.apps import watch
from uav_downloader.apps.watch_categories import find_target
from uav_downloader.core.video_identity import (
    dedupe_video_candidates, normalize_version_preference,
)


def _scan(monkeypatch, tmp_path, fetch, *, first_run=True, seen=None, pref='standard'):
    monkeypatch.setattr(watch, 'load_seen', lambda: seen or {})
    monkeypatch.setattr(watch, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    target = find_target('MissAV', legacy_name='最近更新')
    logs, downloaded = [], []
    worker = watch.SmallToolWorker(logs.append)
    worker._fetch_page_for_site = fetch
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 10, 10, tzinfo=timezone.utc), '2026-10-10')
    worker._download_one = lambda video, dest: downloaded.append((video, dest))
    cfg = {
        'output_folder': str(tmp_path), 'baseline_date': '2026-01-01',
        'first_run_done': not first_run, 'version_preference': pref,
        'selected_targets': [{'site': 'MissAV', 'id': target['id']}],
    }
    result = worker._scan_and_download(cfg)
    return result, downloaded, logs


@pytest.mark.parametrize('first_run,last_page', [(True, 55), (False, 7)])
def test_scan_continues_past_former_page_caps(monkeypatch, tmp_path, first_run, last_page):
    fetched = []
    def fetch(_site, url):
        page = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
        fetched.append(page)
        return ([{'url': f'https://missav.ws/test-{page:03}',
                  'title': f'TEST-{page:03}'}] if page <= last_page else [])
    result, downloaded, _logs = _scan(
        monkeypatch, tmp_path, fetch, first_run=first_run)
    assert result is True
    assert len(downloaded) == last_page
    assert fetched[-1] == last_page + 1


def test_repeated_listing_page_stops_pagination(monkeypatch, tmp_path):
    calls = []
    def fetch(_site, url):
        calls.append(url)
        return [{'url': 'https://missav.ws/test-001', 'title': 'TEST-001'}]
    result, downloaded, logs = _scan(monkeypatch, tmp_path, fetch)
    assert result is True
    assert len(calls) == 2
    assert len(downloaded) == 1
    assert any('repeated listing' in line for line in logs)


def test_all_versions_keeps_variants_but_dedupes_same_variant_across_sites():
    videos = [
        {'_site': 'MissAV', 'url': 'https://missav.ws/test-001'},
        {'_site': 'MissAV', 'url': 'https://missav.ws/test-001-chinese-subtitle'},
        {'_site': 'JableTV', 'url': 'https://jable.tv/videos/test-001/',
         '_versions': ['chinese-subtitle']},
        {'_site': 'MissAV', 'url': 'https://missav.ws/test-001-uncensored-leak'},
    ]
    assert normalize_version_preference('all') == 'all'
    kept, decisions = dedupe_video_candidates(videos, 'all')
    assert len(kept) == 3
    assert len(decisions) == 1
    assert videos[2] in kept


def test_all_versions_reconsiders_skipped_variant_and_separates_destinations(
        monkeypatch, tmp_path):
    standard = 'https://missav.ws/test-001'
    chinese = standard + '-chinese-subtitle'
    seen = {
        standard: {'title': 'TEST-001', 'site': 'MissAV', 'code': 'TEST-001',
                   'versions': ['standard'], 'skipped': False},
        chinese: {'title': 'TEST-001', 'site': 'MissAV', 'code': 'TEST-001',
                  'versions': ['chinese-subtitle'], 'skipped': True,
                  'reason': 'duplicate-version'},
    }
    def fetch(_site, url):
        if 'page=2' in url:
            return []
        return [{'url': standard, 'title': 'TEST-001'},
                {'url': chinese, 'title': 'TEST-001'}]
    result, downloaded, _logs = _scan(
        monkeypatch, tmp_path, fetch, seen=seen, pref='all')
    assert result is True
    assert [video['url'] for video, _dest in downloaded] == [chinese]
    assert downloaded[0][1] == str(tmp_path / 'versions' / 'chinese-subtitle')


def test_scan_can_be_cancelled_before_the_next_page(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    worker = watch.SmallToolWorker(lambda _line: None)
    calls = []
    def fetch(_site, url):
        calls.append(url)
        worker._stop.set()
        return [{'url': 'https://missav.ws/test-001'}]
    worker._fetch_page_for_site = fetch
    target = find_target('MissAV', legacy_name='最近更新')
    cfg = {'output_folder': str(tmp_path), 'baseline_date': '2026-01-01',
           'first_run_done': False,
           'selected_targets': [{'site': 'MissAV', 'id': target['id']}]}
    assert worker._scan_and_download(cfg) is False
    assert len(calls) == 1
    assert cfg['first_run_done'] is False


def test_scan_stops_at_confirmed_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    worker = watch.SmallToolWorker(lambda _line: None)
    calls = []
    def fetch(_site, url):
        calls.append(url)
        return [{'url': f'https://missav.ws/test-{n:03}'} for n in range(10)]
    worker._fetch_page_for_site = fetch
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2025, 1, 1, tzinfo=timezone.utc), '2025-01-01')
    worker._download_one = lambda *_args: pytest.fail('before-baseline download')
    target = find_target('MissAV', legacy_name='最近更新')
    cfg = {'output_folder': str(tmp_path), 'baseline_date': '2026-01-01',
           'first_run_done': False,
           'selected_targets': [{'site': 'MissAV', 'id': target['id']}]}
    assert worker._scan_and_download(cfg) is True
    assert len(calls) == 1


def test_changing_to_all_versions_requests_baseline_rescan(monkeypatch):
    saved = []
    monkeypatch.setattr(watch, 'update_config', lambda patch, **_kw: saved.append(patch))
    app = SimpleNamespace(
        _cfg={'version_preference': 'standard', 'first_run_done': True},
        _version_pref_from_label=lambda _value: 'all')
    watch.SmallToolApp._on_version_change(app, 'All versions')
    assert app._cfg['first_run_done'] is False
    assert saved == [{'version_preference': 'all', 'first_run_done': False}]


def test_all_versions_retains_unknown_release_urls_and_combined_variants():
    videos = [
        {'_site': 'MissAV', 'url': 'https://missav.ws/test-001',
         '_versions': ['chinese-subtitle', 'uncensored']},
        {'_site': 'MissAV', 'url': 'https://missav.ws/test-001-chinese-subtitle'},
        {'_site': 'Hanime1', 'url': 'https://hanime1.me/watch?v=123'},
        {'_site': 'Hanime1', 'url': 'https://hanime1.me/watch?v=456'},
        {'_site': 'Hanime1', 'url': 'https://hanime1.me/watch?v=123'},
    ]
    kept, decisions = dedupe_video_candidates(videos, 'all')
    assert len(kept) == 4
    assert len(decisions) == 1
