import itertools

import pytest

from uav_downloader.core.video_identity import (
    DEFAULT_VERSION_PREFERENCE,
    MAX_SOURCE_SUBTITLE_EVIDENCE_TEXT,
    TRUSTED_CHINESE_SUBTITLE_EVIDENCE,
    VALID_VERSION_PREFERENCES,
    canonical_code,
    dedupe_video_candidates,
    normalize_source_subtitle_evidence,
    normalize_version_preference,
    site_from_url,
    trusted_chinese_subtitle_evidence,
    video_code,
    video_versions,
)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('IPZZ-905', 'ipzz-905'),
    ('[Chinese Subtitles] SNOS-223', 'snos-223'),
    ('FC2PPV 4931572', 'fc2-ppv-4931572'),
    ('FC2-PPV-1657563', 'fc2-ppv-1657563'),
    ('020121_429-paco', '020121-429'),
    ('1pondo-123456_789', '1pondo-123456-789'),
    ('HEYZO-1234 sample title', 'heyzo-1234'),
    ('random article 440577', ''),
    ('no confirmed code', ''),
])
def test_canonical_code_only_returns_confirmed_code_shapes(raw, expected):
    assert canonical_code(raw) == expected


@pytest.mark.parametrize(('video', 'expected'), [
    ({'_site': 'JableTV', 'url': 'https://jable.tv/videos/IPZZ-905/'},
     'ipzz-905'),
    ({'_site': 'MissAV',
      'url': 'https://missav.ai/cn/mimk-284-chinese-subtitle'},
     'mimk-284'),
    ({'_site': 'MissAV',
      'url': 'https://missav.ai/mimk-284-uncensored-leak'},
     'mimk-284'),
    ({'_site': 'SupJav', 'url': 'https://supjav.com/440577.html',
      'title': '[Reducing Mosaic] IPZZ-905'},
     'ipzz-905'),
    ({'_site': 'SupJav', 'url': 'https://supjav.com/440578.html',
      'title': 'FC2PPV 4931572'},
     'fc2-ppv-4931572'),
    ({'_site': 'SupJav', 'url': 'https://supjav.com/440579.html',
      'title': 'random article 440577'},
     ''),
])
def test_video_code_uses_site_specific_evidence(video, expected):
    assert video_code(video) == expected


@pytest.mark.parametrize(('url', 'expected'), [
    ('https://jable.tv/videos/ipzz-905/', 'JableTV'),
    ('https://cn.jable.tv/videos/ipzz-905/', 'JableTV'),
    ('https://missav.ai/mimk-284', 'MissAV'),
    ('https://missav.ws/mimk-284', 'MissAV'),
    ('https://www.missav.ai/test-123', 'MissAV'),
    ('https://www.missav.ws/test-123', 'MissAV'),
    ('https://www.missav.live/test-123', 'MissAV'),
    ('https://supjav.com/440577.html', 'SupJav'),
    ('https://example.com/ipzz-905', ''),
])
def test_site_from_url(url, expected):
    assert site_from_url(url) == expected


@pytest.mark.parametrize(('video', 'expected'), [
    ({
        'url': 'https://missav.ai/cn/mimk-284-chinese-subtitle',
    }, ('missav-url-chinese-subtitle',)),
    ({
        'url': 'https://missav.ai/cn/mimk-284-chinese-subtitles',
    }, ('missav-url-chinese-subtitle',)),
    ({
        '_site': 'JableTV',
        '_target_id': 'category:chinese-subtitle',
        'url': 'https://jable.tv/videos/ipzz-905/',
    }, ('jable-category-chinese-subtitle',)),
    ({
        '_site': 'MissAV',
        '_target_id': 'feed:chinese-subtitle',
        'url': 'https://missav.ws/ipzz-905',
    }, ('missav-category-chinese-subtitle',)),
    ({
        '_site': 'SupJav',
        '_target_id': 'category:chinese-subtitles',
        'url': 'https://supjav.com/440577.html',
    }, ('supjav-category-chinese-subtitle',)),
    ({
        '_site': 'JableTV',
        'url': 'https://jable.tv/videos/ipzz-905/',
        '_source_listing_url':
            'https://jable.tv/categories/chinese-subtitle/',
    }, ('jable-category-chinese-subtitle',)),
    ({
        '_site': 'MissAV',
        'url': 'https://missav.ai/ipzz-905',
        '_source_listing_url':
            'https://missav.ai/dm278/cn/chinese-subtitle',
    }, ('missav-category-chinese-subtitle',)),
    ({
        '_site': 'SupJav',
        'url': 'https://supjav.com/440577.html',
        '_source_listing_url':
            'https://supjav.com/zh/category/chinese-subtitles',
    }, ('supjav-category-chinese-subtitle',)),
])
def test_trusted_chinese_subtitle_evidence_uses_structural_source_signals(
        video, expected):
    assert trusted_chinese_subtitle_evidence(video) == expected


@pytest.mark.parametrize('video', [
    # /cn/ controls the MissAV site language; it is not subtitle evidence.
    {'url': 'https://missav.ai/cn/mimk-284'},
    # Display-title heuristics remain useful for browse badges/deduplication
    # but are intentionally too weak to suppress requested work.
    {'url': 'https://supjav.com/1.html',
     'title': '[Chinese Subtitles] IPZZ-905'},
    {'url': 'https://jable.tv/videos/ipzz-905/',
     'title': 'A discussion about 中文字幕'},
    {'url': 'https://jable.tv/videos/ipzz-905-c/'},
    {
        '_site': 'JableTV',
        '_target_id': 'feed:latest',
        'url': 'https://jable.tv/videos/ipzz-905/',
    },
    {
        '_site': 'MissAV',
        '_target_id': 'feed:latest',
        'url': 'https://missav.ai/ipzz-905',
    },
    {
        '_site': 'SupJav',
        '_target_id': 'category:reducing-mosaic',
        'url': 'https://supjav.com/1.html',
    },
    {
        '_site': 'MissAV',
        'url': 'https://missav.ai/ipzz-905',
        '_hls_subtitle_renditions': [
            {'language': 'zh', 'uri': 'subtitles/zh.m3u8'},
        ],
    },
    {'url': 'https://missav.evil.example/ipzz-905-chinese-subtitle'},
    {'url': 'https://example.test/ipzz-905-chinese-subtitle'},
    {'url': 'http://missav.ai/ipzz-905-chinese-subtitle'},
    {
        '_site': 'JableTV',
        '_target_id': 'category:chinese-subtitle',
    },
    {
        'url': 'https://evil.jable.tv/videos/ipzz-905/',
        '_source_subtitle_evidence':
            'jable-category-chinese-subtitle',
    },
    {
        'url': 'https://missav.evil.example/ipzz-905',
        '_source_subtitle_evidence':
            'missav-category-chinese-subtitle',
    },
    {
        'url': 'https://supjav.evil.example/1.html',
        '_source_subtitle_evidence':
            'supjav-category-chinese-subtitle',
    },
    {
        '_site': 'SupJav',
        'url': 'https://supjav.com/1.html',
        '_source_listing_url':
            'http://supjav.com/category/chinese-subtitles',
    },
    {
        '_site': 'JableTV',
        '_target_id': 'category:chinese-subtitle',
        'url': 'https://supjav.com/1.html',
    },
])
def test_trusted_chinese_subtitle_evidence_fails_closed(video):
    assert trusted_chinese_subtitle_evidence(video) == ()


def test_source_subtitle_evidence_normalizer_is_allowlisted_and_bounded():
    expected = tuple(sorted(TRUSTED_CHINESE_SUBTITLE_EVIDENCE))
    assert normalize_source_subtitle_evidence(
        '|'.join(reversed(expected))) == expected
    assert normalize_source_subtitle_evidence(
        ['missav-url-chinese-subtitle', 'forged']) == (
            'missav-url-chinese-subtitle',)
    assert normalize_source_subtitle_evidence(
        'x' * (MAX_SOURCE_SUBTITLE_EVIDENCE_TEXT + 1)) == ()
    assert normalize_source_subtitle_evidence(
        ['missav-url-chinese-subtitle']
        * (len(TRUSTED_CHINESE_SUBTITLE_EVIDENCE) + 1)) == ()
    assert normalize_source_subtitle_evidence(
        '|'.join(
            ['missav-url-chinese-subtitle']
            * (len(TRUSTED_CHINESE_SUBTITLE_EVIDENCE) + 1))) == ()


@pytest.mark.parametrize(('video', 'expected'), [
    ({'_target_id': 'category:chinese-subtitle'}, {'chinese-subtitle'}),
    ({'_target_id': 'feed:chinese-subtitle'}, {'chinese-subtitle'}),
    ({'_target_id': 'category:uncensored'}, {'uncensored'}),
    ({'_target_id': 'feed:uncensored-leak'}, {'uncensored'}),
    ({'_target_id': 'category:censored'}, {'standard'}),
    ({'_target_id': 'category:english-subtitles'}, {'english-subtitle'}),
    ({'_target_id': 'category:reducing-mosaic'}, {'reducing-mosaic'}),
    ({'url': 'https://missav.ai/mimk-284-chinese-subtitle'},
     {'chinese-subtitle'}),
    ({'url': 'https://missav.ai/mimk-284-uncensored-leak'},
     {'uncensored'}),
    ({'title': '[English Subtitles] FSDSS-622'}, {'english-subtitle'}),
    ({'title': '[Reduced Mosaic] IPZZ-905'}, {'reducing-mosaic'}),
    ({'title': '[Uncensored] FC2PPV 4937463'}, {'uncensored'}),
    ({'title': 'DLDSS-507'}, {'standard'}),
])
def test_video_versions_cover_all_supported_sources(video, expected):
    assert video_versions(video) == expected


def _version_candidates():
    return [
        {'_site': 'JableTV', '_target_id': 'category:chinese-subtitle',
         'url': 'https://jable.tv/videos/ipzz-905/', 'title': 'IPZZ-905'},
        {'_site': 'MissAV', '_target_id': 'feed:uncensored-leak',
         'url': 'https://missav.ai/ipzz-905-uncensored-leak',
         'title': 'IPZZ-905'},
        {'_site': 'MissAV', '_target_id': 'feed:latest',
         'url': 'https://missav.ai/ipzz-905', 'title': 'IPZZ-905'},
        {'_site': 'SupJav', '_target_id': 'category:english-subtitles',
         'url': 'https://supjav.com/440576.html',
         'title': '[English Subtitles] IPZZ-905'},
        {'_site': 'SupJav', '_target_id': 'category:reducing-mosaic',
         'url': 'https://supjav.com/440577.html',
         'title': '[Reducing Mosaic] IPZZ-905'},
    ]


@pytest.mark.parametrize(('preference', 'expected_url'), [
    ('chinese-subtitle', 'https://jable.tv/videos/ipzz-905/'),
    ('uncensored', 'https://missav.ai/ipzz-905-uncensored-leak'),
    ('standard', 'https://missav.ai/ipzz-905'),
    ('english-subtitle', 'https://supjav.com/440576.html'),
    ('reducing-mosaic', 'https://supjav.com/440577.html'),
])
def test_preference_wins_across_sources_regardless_of_scan_order(
        preference, expected_url):
    candidates = _version_candidates()
    for order in itertools.permutations(candidates):
        kept, decisions = dedupe_video_candidates(
            [dict(video) for video in order], preference)
        assert [video['url'] for video in kept] == [expected_url]
        assert len(decisions) == len(candidates) - 1


def test_same_url_across_categories_unions_version_evidence():
    latest = {
        '_site': 'JableTV', '_target_id': 'feed:latest',
        'url': 'https://jable.tv/videos/ipzz-905/', 'title': 'IPZZ-905',
    }
    chinese = {
        '_site': 'JableTV', '_target_id': 'category:chinese-subtitle',
        'url': latest['url'], 'title': 'IPZZ-905',
    }
    standard_elsewhere = {
        '_site': 'MissAV', '_target_id': 'feed:latest',
        'url': 'https://missav.ai/ipzz-905', 'title': 'IPZZ-905',
    }

    kept, _ = dedupe_video_candidates(
        [latest, standard_elsewhere, chinese], 'chinese-subtitle')

    assert kept == [latest]
    assert latest['_versions'] == ['chinese-subtitle']
    assert chinese['_versions'] == ['chinese-subtitle']
    assert latest['_source_subtitle_evidence'] == (
        'jable-category-chinese-subtitle',)
    assert chinese['_source_subtitle_evidence'] == (
        'jable-category-chinese-subtitle',)
    assert trusted_chinese_subtitle_evidence(latest) == (
        'jable-category-chinese-subtitle',)


def test_same_url_title_heuristic_does_not_become_source_subtitle_evidence():
    plain = {
        '_site': 'JableTV', '_target_id': 'feed:latest',
        'url': 'https://jable.tv/videos/ipzz-905/', 'title': 'IPZZ-905',
    }
    title_only = {
        '_site': 'JableTV', '_target_id': 'feed:latest',
        'url': plain['url'], 'title': '[Chinese Subtitles] IPZZ-905',
    }

    kept, _ = dedupe_video_candidates(
        [plain, title_only], 'chinese-subtitle')

    assert kept == [plain]
    assert trusted_chinese_subtitle_evidence(plain) == ()


def test_same_version_cross_site_tie_has_stable_source_order():
    candidates = [
        {'_site': 'SupJav', '_target_id': 'category:chinese-subtitles',
         'url': 'https://supjav.com/1.html', 'title': 'IPZZ-905'},
        {'_site': 'MissAV', '_target_id': 'feed:chinese-subtitle',
         'url': 'https://missav.ai/ipzz-905-chinese-subtitle',
         'title': 'IPZZ-905'},
        {'_site': 'JableTV', '_target_id': 'category:chinese-subtitle',
         'url': 'https://jable.tv/videos/ipzz-905/', 'title': 'IPZZ-905'},
    ]
    for order in itertools.permutations(candidates):
        kept, _ = dedupe_video_candidates(
            [dict(video) for video in order], 'chinese-subtitle')
        assert kept[0]['_site'] == 'JableTV'


def test_successful_preferred_download_prevents_cross_site_redownload():
    existing = {
        '_site': 'JableTV', '_target_id': 'category:chinese-subtitle',
        'url': 'https://jable.tv/videos/ipzz-905/', 'title': 'IPZZ-905',
        '_already_seen': True,
    }
    new = {
        '_site': 'MissAV', '_target_id': 'feed:chinese-subtitle',
        'url': 'https://missav.ai/ipzz-905-chinese-subtitle',
        'title': 'IPZZ-905',
    }

    kept, _ = dedupe_video_candidates(
        [existing, new], 'chinese-subtitle')

    assert kept == [existing]


def test_new_preferred_version_can_upgrade_prior_unpreferred_download():
    existing = {
        '_site': 'JableTV', '_target_id': 'feed:latest',
        'url': 'https://jable.tv/videos/ipzz-905/', 'title': 'IPZZ-905',
        '_already_seen': True,
    }
    new = {
        '_site': 'SupJav', '_target_id': 'category:reducing-mosaic',
        'url': 'https://supjav.com/440577.html', 'title': 'IPZZ-905',
    }

    kept, _ = dedupe_video_candidates([existing, new], 'reducing-mosaic')

    assert kept == [new]


def test_unconfirmed_codes_only_dedupe_by_exact_url():
    first = {'_site': 'SupJav', 'url': 'https://example.com/a',
             'title': 'unknown release'}
    same_url = {'_site': 'MissAV', 'url': 'https://example.com/a',
                'title': 'another unknown release'}
    different_url = {'_site': 'JableTV', 'url': 'https://example.com/b',
                     'title': 'unknown release'}

    kept, decisions = dedupe_video_candidates(
        [first, same_url, different_url], DEFAULT_VERSION_PREFERENCE)

    assert [video['url'] for video in kept] == [
        'https://example.com/a', 'https://example.com/b']
    assert len(decisions) == 1


@pytest.mark.parametrize('value', [None, '', 'bogus', object()])
def test_invalid_preferences_fall_back_to_chinese(value):
    assert normalize_version_preference(value) == DEFAULT_VERSION_PREFERENCE


def test_legacy_uncensored_value_is_normalized():
    assert normalize_version_preference('uncensored-leak') == 'uncensored'
    assert VALID_VERSION_PREFERENCES == {
        'chinese-subtitle', 'uncensored', 'standard',
        'english-subtitle', 'reducing-mosaic',
    }
