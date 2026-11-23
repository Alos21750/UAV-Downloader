from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from uav_downloader.apps import watch
from uav_downloader.apps.watch_categories import SITES, find_target, selection_key


def test_role_targets_are_persistable_and_share_a_real_feed():
    group = next(g for g in SITES['MissAV']['groups'] if g['id'] == 'roles')
    feed = find_target('MissAV', 'feed:latest')
    assert len(group['targets']) == 13
    for target in group['targets']:
        assert selection_key('MissAV', target['id']) == 'MissAV|role:' + target['role_slug']
        assert find_target('MissAV', target['id']) == target
        assert target['url'] == feed['url']
        assert find_target('MissAV', target['id'], legacy_name='最近更新') is target


@pytest.mark.parametrize('also_latest', [False, True])
def test_multi_role_watch_scan_uses_one_listing_and_keeps_site_controls(
        monkeypatch, tmp_path, also_latest):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'save_seen', lambda _: None)
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'T', lambda key, **kw: '' if key == 'missav_lang' else key)
    worker = watch.SmallToolWorker(lambda _: None)
    pages, dates, downloads = [], [], []
    def fetch(site, url):
        parsed = urlsplit(url)
        page = int(parse_qs(parsed.query).get('page', ['1'])[0])
        assert site == 'MissAV' and parsed.path == '/dm539/new'
        pages.append(page)
        titles = {1: ('Morning walk',), 2: ('Nurse and flight attendant', 'A nurse revisits'),
                  3: ('Nurse at the practice',)}.get(page, ())
        return [dict(title=t, url=f'https://missav.ai/test-{page}{i}1')
                for i, t in enumerate(titles)]
    worker._fetch_page_for_site = fetch
    def fetch_date(url):
        dates.append(url)
        before = url.endswith('301')
        return (datetime(2025 if before else 2026, 10, 1, tzinfo=timezone.utc), 'date')
    worker._fetch_missav_video_date = fetch_date
    worker._download_one = lambda v, dest: downloads.append((v, dest))
    selections = ['role:nurse', 'role:flight-attendant'] + (['feed:latest'] if also_latest else [])
    cfg = {'output_folder': str(tmp_path), 'baseline_date': '2024-01-01',
           'site_settings': {'MissAV': {'output_folder': str(tmp_path/'site'), 'baseline_date': '2026-01-01'}},
           'first_run_done': True, 'version_preference': 'standard',
           'selected_targets': [{'site':'MissAV', 'id':key, 'category':'最近更新'} for key in selections]}
    assert worker._scan_and_download(cfg)
    assert pages == [1, 2, 3, 4]
    assert len(dates) == (4 if also_latest else 3)
    assert {v['url'] for v, _ in downloads} == {
        'https://missav.ai/test-201', 'https://missav.ai/test-211',
        *(['https://missav.ai/test-101'] if also_latest else [])}
    assert len(downloads) == (3 if also_latest else 2)
    assert all(dest == str(tmp_path/'site') for _, dest in downloads)
    assert also_latest or 'https://missav.ai/test-101' not in worker._seen


def test_role_only_sparse_feed_is_bounded_without_touching_history(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    worker = watch.SmallToolWorker(lambda _: None)
    calls = []
    def fetch(_site, url):
        calls.append(url)
        return [{'title': 'Plain title', 'url': f'https://missav.ai/test-{len(calls)}'}]
    worker._fetch_page_for_site = fetch
    worker._fetch_missav_video_date = lambda *_: pytest.fail('Unrelated card got a detail request')
    cfg = {'output_folder': str(tmp_path), 'first_run_done': False,
           'selected_targets': [{'site':'MissAV', 'id':'role:nurse'}]}
    assert worker._scan_and_download(cfg)
    assert len(calls) == 10
    assert not worker._seen
