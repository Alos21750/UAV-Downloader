from datetime import datetime, timezone
from types import SimpleNamespace

from uav_downloader.apps import watch as jable_smalltool
from uav_downloader.apps.watch_categories import find_target
from uav_downloader.core.video_identity import trusted_chinese_subtitle_evidence


def _exact_missav_targets():
    targets = []
    for name in ('亂倫', 'NTR'):
        target = find_target('MissAV', None, name)
        assert target is not None
        targets.append({
            'site': 'MissAV',
            'id': target['id'],
            'category': target['name'],
        })
    return targets


def test_exact_missav_monitoring_run_reports_scan_and_downloads(monkeypatch,
                                                                 tmp_path):
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)

    logs = []
    statuses = []
    worker = jable_smalltool.SmallToolWorker(
        logs.append, lambda key, _color: statuses.append(key))
    scan_states = []
    original_set_scan_state = worker._set_scan_state

    def capture_scan_state(*args, **kwargs):
        original_set_scan_state(*args, **kwargs)
        scan_states.append(worker.get_scan_state())

    worker._set_scan_state = capture_scan_state
    fetch_calls = []

    def fake_fetch(site_name, url):
        assert site_name == 'MissAV'
        fetch_calls.append(url)
        if 'page=2' in url:
            return []
        slug = 'ntr-sample' if '/NTR' in url else 'incest-sample'
        return [{
            'url': f'https://missav.ai/{slug}-001',
            'title': slug,
        }]

    worker._fetch_page_for_site = fake_fetch
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 7, 11, tzinfo=timezone.utc), '2026-07-11')
    downloaded = []
    worker._download_one = lambda video, dest: downloaded.append(
        (video['url'], dest))

    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'first_run_done': False,
        'selected_targets': _exact_missav_targets(),
    }

    assert worker._scan_and_download(cfg) is True
    assert len(downloaded) == 2
    assert statuses[0] == 'st_scanning'
    assert 'st_downloading' in statuses
    assert any(state and state[0] == 'MissAV' and state[2] == 1
               for state in scan_states)
    assert any(state and state[3] == 2 for state in scan_states)
    assert worker.get_scan_state() is None
    assert any('/dm57/' in url and '/genres/' in url for url in fetch_calls)
    assert any('/dm757/' in url and '/NTR' in url for url in fetch_calls)
    assert any('First run' in line and '2026-04-11' in line for line in logs)


def test_worker_uses_independent_dates_and_output_folders_per_site(
        monkeypatch, tmp_path):
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._fetch_page_for_site = lambda site, _url: [{
        'url': (
            'https://jable.tv/videos/jable-sample/' if site == 'JableTV'
            else 'https://missav.ai/missav-sample'),
        'title': f'{site} sample',
    }]
    worker._fetch_video_date = lambda _url: (
        datetime(2026, 7, 15, tzinfo=timezone.utc), '2026-07-15')
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 8, 15, tzinfo=timezone.utc), '2026-08-15')
    downloaded = []
    worker._download_one = lambda video, dest: downloaded.append(
        (video['_site'], dest))

    jable_target = find_target('JableTV', legacy_name='最近更新')
    missav_target = find_target('MissAV', legacy_name='最近更新')
    cfg = {
        'output_folder': str(tmp_path / 'shared'),
        'baseline_date': '2026-08-01',
        'site_settings': {
            'JableTV': {
                'output_folder': str(tmp_path / 'jable'),
                'baseline_date': '2026-07-01',
            },
            'MissAV': {
                'output_folder': str(tmp_path / 'missav'),
            },
        },
        'first_run_done': False,
        'selected_targets': [
            {'site': 'JableTV', 'id': jable_target['id'],
             'category': jable_target['name']},
            {'site': 'MissAV', 'id': missav_target['id'],
             'category': missav_target['name']},
        ],
    }

    assert worker._scan_and_download(cfg) is True
    assert downloaded == [
        ('JableTV', str(tmp_path / 'jable')),
        ('MissAV', str(tmp_path / 'missav')),
    ]


def test_missav_same_code_uses_selected_version_regardless_of_order():
    standard = {
        '_site': 'MissAV',
        'url': 'https://missav.ai/mimk-284',
        'title': 'MIMK-284',
    }
    subtitle = {
        '_site': 'MissAV',
        'url': 'https://missav.ai/cn/mimk-284-chinese-subtitle',
        'title': 'MIMK-284',
    }
    leaked = {
        '_site': 'MissAV',
        'url': 'https://missav.ai/mimk-284-uncensored-leak',
        'title': 'MIMK-284',
    }
    unrelated = {
        '_site': 'MissAV',
        'url': 'https://missav.ai/fc2-ppv-1234567',
        'title': 'FC2-PPV-1234567',
    }

    for preference, expected in (
            ('chinese-subtitle', subtitle),
            ('uncensored-leak', leaked),
            ('standard', standard)):
        for ordered in ([standard, subtitle, leaked, unrelated],
                        [leaked, subtitle, standard, unrelated]):
            kept, decisions = jable_smalltool._dedupe_missav_candidates(
                ordered, preference)
            kept_urls = [video['url'] for video in kept]

            assert kept_urls.count(expected['url']) == 1
            assert sum('mimk-284' in url for url in kept_urls) == 1
            assert unrelated['url'] in kept_urls
            assert len(decisions) == 2
            assert all(code == 'mimk-284'
                       for _dropped, _kept, code in decisions)

    default_kept, _ = jable_smalltool._dedupe_missav_candidates(
        [standard, leaked, subtitle])
    assert default_kept == [subtitle]

    assert jable_smalltool._missav_video_code(unrelated) == 'fc2-ppv-1234567'


def test_worker_defaults_to_chinese_subtitle_for_duplicate_missav_code(
        monkeypatch, tmp_path):
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)

    logs = []
    worker = jable_smalltool.SmallToolWorker(logs.append)
    worker._fetch_page_for_site = lambda _site, url: ([] if 'page=2' in url else [
        {'url': 'https://missav.ai/hone-297', 'title': 'HONE-297'},
        {'url': 'https://missav.ai/hone-297-uncensored-leak', 'title': 'HONE-297'},
        {'url': 'https://missav.ai/hone-297-chinese-subtitle', 'title': 'HONE-297'},
    ])
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 7, 11, tzinfo=timezone.utc), '2026-07-11')
    downloaded = []
    worker._download_one = lambda video, dest: downloaded.append(
        (video['url'], dest))

    target = find_target('MissAV', None, '亂倫')
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'first_run_done': False,
        'selected_targets': [{
            'site': 'MissAV', 'id': target['id'], 'category': target['name'],
        }],
    }

    assert worker._scan_and_download(cfg) is True
    assert downloaded == [(
        'https://missav.ai/hone-297-chinese-subtitle', str(tmp_path))]
    assert worker._seen['https://missav.ai/hone-297']['skipped'] is True
    assert worker._seen[
        'https://missav.ai/hone-297-uncensored-leak']['skipped'] is True
    assert any('[DEDUP] hone-297' in line for line in logs)


def test_chinese_preference_scans_missav_genre_filter_before_fallback(
        monkeypatch, tmp_path):
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    fetch_calls = []

    def fake_fetch(_site, url):
        fetch_calls.append(url)
        suffix = ('-chinese-subtitle'
                  if 'filters=chinese-subtitle' in url else '')
        return [{
            'url': f'https://missav.ai/hone-297{suffix}',
            'title': 'HONE-297',
        }]

    worker._fetch_page_for_site = fake_fetch
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 7, 11, tzinfo=timezone.utc), '2026-07-11')
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    target = find_target('MissAV', None, '亂倫')
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'chinese-subtitle',
        'first_run_done': False,
        'selected_targets': [{
            'site': 'MissAV', 'id': target['id'], 'category': target['name'],
        }],
    }

    assert worker._scan_and_download(cfg) is True
    assert 'filters=chinese-subtitle' in fetch_calls[0]
    assert any('filters=chinese-subtitle' not in url for url in fetch_calls)
    assert [video['url'] for video in downloaded] == [
        'https://missav.ai/hone-297-chinese-subtitle']


def test_changed_preference_reconsiders_version_skipped_by_dedup(
        monkeypatch, tmp_path):
    subtitle_url = 'https://missav.ai/mimk-284-chinese-subtitle'
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {
        'https://missav.ai/mimk-284': {
            'title': 'MIMK-284', 'skipped': False,
        },
        subtitle_url: {
            'title': 'MIMK-284', 'skipped': True,
            'reason': 'duplicate-version',
        },
    })
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._fetch_page_for_site = lambda _site, url: ([] if 'page=2' in url else [
        {'url': subtitle_url, 'title': 'MIMK-284'},
    ])
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 7, 12, tzinfo=timezone.utc), '2026-07-12')
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video['url'])
    target = find_target('MissAV', None, '亂倫')
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'missav_version_preference': 'chinese-subtitle',
        'first_run_done': False,
        'selected_targets': [{
            'site': 'MissAV', 'id': target['id'], 'category': target['name'],
        }],
    }

    assert worker._scan_and_download(cfg) is True
    assert downloaded == [subtitle_url]


def _target(site, target_id):
    found = find_target(site, target_id, None)
    assert found is not None
    return {'site': site, 'id': found['id'], 'category': found['name']}


def test_worker_dedupes_same_code_across_categories_and_all_sites(
        monkeypatch, tmp_path):
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)

    def fake_fetch(site, url):
        if site == 'JableTV':
            return [{
                'url': 'https://jable.tv/videos/ipzz-905/',
                'title': 'IPZZ-905',
            }]
        if site == 'MissAV':
            suffix = ('-chinese-subtitle'
                      if 'chinese-subtitle' in url else '')
            return [{
                'url': f'https://missav.ai/ipzz-905{suffix}',
                'title': 'IPZZ-905',
            }]
        return [{
            'url': 'https://supjav.com/440577.html',
            'title': '[Reducing Mosaic] IPZZ-905',
            'date': '2026/07/12',
        }]

    worker._fetch_page_for_site = fake_fetch
    worker._fetch_video_date = lambda _url: (
        datetime(2026, 7, 12, tzinfo=timezone.utc), 'today')
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 7, 12, tzinfo=timezone.utc), '2026-07-12')
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'reducing-mosaic',
        'first_run_done': False,
        'selected_targets': [
            _target('JableTV', 'feed:latest'),
            _target('JableTV', 'category:chinese-subtitle'),
            _target('MissAV', 'feed:latest'),
            _target('MissAV', 'feed:chinese-subtitle'),
            _target('SupJav', 'category:reducing-mosaic'),
        ],
    }

    assert worker._scan_and_download(cfg) is True
    assert [video['url'] for video in downloaded] == [
        'https://supjav.com/440577.html']
    assert worker._seen['https://jable.tv/videos/ipzz-905/'][
        'reason'] == 'duplicate-version'
    assert worker._seen['https://jable.tv/videos/ipzz-905/'][
        'versions'] == ['chinese-subtitle']
    assert worker._seen['https://missav.ai/ipzz-905'][
        'code'] == 'ipzz-905'


def test_worker_same_url_preserves_trusted_chinese_listing_evidence(
        monkeypatch, tmp_path):
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {})
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    video_url = 'https://jable.tv/videos/ipzz-905/'
    worker._fetch_page_for_site = lambda _site, _url: [{
        'url': video_url,
        'title': 'IPZZ-905',
    }]
    worker._fetch_video_date = lambda _url: (
        datetime(2026, 7, 12, tzinfo=timezone.utc), 'today')
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'chinese-subtitle',
        'first_run_done': False,
        'selected_targets': [
            _target('JableTV', 'feed:latest'),
            _target('JableTV', 'category:chinese-subtitle'),
        ],
    }

    assert worker._scan_and_download(cfg) is True
    assert len(downloaded) == 1
    assert downloaded[0]['url'] == video_url
    assert trusted_chinese_subtitle_evidence(downloaded[0]) == (
        'jable-category-chinese-subtitle',)


def test_prior_preferred_download_blocks_cross_site_duplicate(
        monkeypatch, tmp_path):
    existing_url = 'https://jable.tv/videos/ipzz-905/'
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {
        existing_url: {
            'title': 'IPZZ-905', 'skipped': False, 'site': 'JableTV',
            'code': 'ipzz-905', 'versions': ['chinese-subtitle'],
        },
    })
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    candidate_url = 'https://missav.ai/ipzz-905-chinese-subtitle'
    worker._fetch_page_for_site = lambda _site, _url: [{
        'url': candidate_url, 'title': 'IPZZ-905',
    }]
    worker._fetch_missav_video_date = lambda _url: (
        datetime(2026, 7, 12, tzinfo=timezone.utc), '2026-07-12')
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'chinese-subtitle',
        'first_run_done': False,
        'selected_targets': [_target('MissAV', 'feed:chinese-subtitle')],
    }

    assert worker._scan_and_download(cfg) is True
    assert downloaded == []
    assert worker._seen[existing_url]['skipped'] is False
    assert worker._seen[candidate_url]['reason'] == 'duplicate-version'


def test_new_preferred_cross_site_version_upgrades_prior_download(
        monkeypatch, tmp_path):
    existing_url = 'https://jable.tv/videos/ipzz-905/'
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {
        existing_url: {
            'title': 'IPZZ-905', 'skipped': False, 'site': 'JableTV',
            'code': 'ipzz-905', 'versions': ['standard'],
        },
    })
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    preferred_url = 'https://supjav.com/440577.html'
    worker._fetch_page_for_site = lambda _site, _url: [{
        'url': preferred_url, 'title': '[Reducing Mosaic] IPZZ-905',
        'date': '2026/07/12',
    }]
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'reducing-mosaic',
        'first_run_done': False,
        'selected_targets': [
            _target('SupJav', 'category:reducing-mosaic')],
    }

    assert worker._scan_and_download(cfg) is True
    assert [video['url'] for video in downloaded] == [preferred_url]
    assert worker._seen[existing_url]['skipped'] is False


def test_changed_preference_reconsiders_supjav_duplicate(monkeypatch, tmp_path):
    preferred_url = 'https://supjav.com/440577.html'
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {
        preferred_url: {
            'title': '[Reducing Mosaic] IPZZ-905', 'skipped': True,
            'reason': 'duplicate-version', 'site': 'SupJav',
            'code': 'ipzz-905', 'versions': ['reducing-mosaic'],
        },
    })
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._fetch_page_for_site = lambda _site, _url: [{
        'url': preferred_url, 'title': '[Reducing Mosaic] IPZZ-905',
        'date': '2026/07/12',
    }]
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'reducing-mosaic',
        'first_run_done': False,
        'selected_targets': [
            _target('SupJav', 'category:reducing-mosaic')],
    }

    assert worker._scan_and_download(cfg) is True
    assert [video['url'] for video in downloaded] == [preferred_url]


def test_earlier_baseline_reconsiders_previously_too_old_video(
        monkeypatch, tmp_path):
    video_url = 'https://supjav.com/440577.html'
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {
        video_url: {
            'title': 'IPZZ-905', 'skipped': True,
            'reason': 'before-baseline', 'release_date': '2026-01-01',
            'site': 'SupJav', 'code': 'ipzz-905',
            'versions': ['standard'],
        },
    })
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._fetch_page_for_site = lambda _site, _url: [{
        'url': video_url, 'title': 'IPZZ-905', 'date': '2026/01/01',
    }]
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2025-12-01',
        'version_preference': 'standard',
        'first_run_done': False,
        'selected_targets': [_target('SupJav', 'feed:latest')],
    }

    assert worker._scan_and_download(cfg) is True
    assert [video['url'] for video in downloaded] == [video_url]


def test_same_baseline_keeps_previously_too_old_video_skipped(
        monkeypatch, tmp_path):
    video_url = 'https://supjav.com/440577.html'
    original_entry = {
        'title': 'IPZZ-905', 'skipped': True,
        'reason': 'before-baseline', 'release_date': '2026-01-01',
        'site': 'SupJav', 'code': 'ipzz-905', 'versions': ['standard'],
    }
    monkeypatch.setattr(jable_smalltool, 'load_seen', lambda: {
        video_url: dict(original_entry),
    })
    monkeypatch.setattr(jable_smalltool, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(jable_smalltool, 'save_config', lambda _cfg: None)
    monkeypatch.setattr(jable_smalltool, 'MAX_SCAN_PAGES', 1)

    worker = jable_smalltool.SmallToolWorker(lambda _line: None)
    worker._fetch_page_for_site = lambda _site, _url: [{
        'url': video_url, 'title': 'IPZZ-905', 'date': '2026/01/01',
    }]
    downloaded = []
    worker._download_one = lambda video, _dest: downloaded.append(video)
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-04-11',
        'version_preference': 'standard',
        'first_run_done': False,
        'selected_targets': [_target('SupJav', 'feed:latest')],
    }

    assert worker._scan_and_download(cfg) is True
    assert downloaded == []
    assert worker._seen[video_url] == original_entry


class _FakeWidget:
    def __init__(self):
        self.visible = True

    def grid(self):
        self.visible = True

    def grid_remove(self):
        self.visible = False


class _FakePackable:
    def __init__(self):
        self.packed = True
        self.pack_kwargs = None
        self.config = {}

    def pack(self, **kwargs):
        self.packed = True
        self.pack_kwargs = kwargs

    def pack_forget(self):
        self.packed = False

    def pack_configure(self, **kwargs):
        self.pack_kwargs = kwargs

    def configure(self, **kwargs):
        self.config.update(kwargs)


def test_category_filter_hides_empty_groups_instead_of_blank_space():
    app = jable_smalltool.SmallToolApp.__new__(jable_smalltool.SmallToolApp)
    ntr = _FakeWidget()
    other = _FakeWidget()
    matching_frame = _FakePackable()
    empty_frame = _FakePackable()
    app._category_filter_var = SimpleNamespace(get=lambda: 'NTR')
    app._filter_groups = [
        {'frame': empty_frame, 'items': [(other, 'incest')]},
        {'frame': matching_frame, 'items': [(ntr, 'ntr')]},
    ]

    app._filter_targets()

    assert other.visible is False
    assert empty_frame.packed is False
    assert ntr.visible is True
    assert matching_frame.packed is True


def test_monitoring_can_collapse_and_restore_category_panel():
    app = jable_smalltool.SmallToolApp.__new__(jable_smalltool.SmallToolApp)
    app._categories_collapsed = False
    app._selection_panel = _FakePackable()
    app._category_tabview = _FakePackable()
    app._category_filter_box = _FakePackable()
    app._categories_toggle_btn = _FakePackable()

    app._set_categories_collapsed(True)

    assert app._categories_collapsed is True
    assert app._category_tabview.packed is False
    assert app._category_filter_box.packed is False
    assert app._selection_panel.pack_kwargs == {'fill': 'x', 'expand': False}
    assert app._categories_toggle_btn.config['text'] == jable_smalltool.T(
        'st_categories_expand')

    app._set_categories_collapsed(False)

    assert app._categories_collapsed is False
    assert app._category_tabview.packed is True
    assert app._category_filter_box.packed is True
    assert app._selection_panel.pack_kwargs == {
        'fill': 'both', 'expand': True}
    assert app._categories_toggle_btn.config['text'] == jable_smalltool.T(
        'st_categories_collapse')
