"""Offline registry, identity, CLI and Web contracts for Huangguo."""

import pytest

from uav_downloader import sites
from uav_downloader.apps import downloads
from uav_downloader.cli.args import get_parser
from uav_downloader.core.video_identity import (
    dedupe_video_candidates, site_from_url, trusted_chinese_subtitle_evidence,
    video_code, video_versions,
)
from uav_downloader.web.queue import QueueManager, validate_job


@pytest.mark.parametrize('url,expected', [
    ('https://huangguoai.com/video/123/', 'huangguo-123-ep-1'),
    ('https://www.huangguoai.com/video/123', 'huangguo-123-ep-1'),
    ('https://huangguoai.com/video/123/ep-1/', 'huangguo-123-ep-1'),
    ('https://huangguoai.com/video/123/ep-2/', 'huangguo-123-ep-2'),
    ('https://www.huangguoai.com/video/123/ep-20', 'huangguo-123-ep-20'),
    ('https://huangguoai.com:443/video/123/ep-2/', 'huangguo-123-ep-2'),
])
def test_registry_accepts_episode_urls(url, expected):
    adapter = sites.validate_url(url)
    assert adapter is not None
    assert adapter.__name__ == 'SiteHuangguo'
    assert adapter in sites.FEATURED_SITE_CLASSES
    assert adapter.validate_url(url) == expected
    assert validate_job({'url': url})['url'] == url


@pytest.mark.parametrize('url', [
    'http://huangguoai.com/video/123/',
    'https://huangguoai.com.evil.test/video/123/',
    'https://evil.huangguoai.com/video/123/',
    'https://user:pass@huangguoai.com/video/123/',
    'https://@huangguoai.com/video/123/',
    'https://:@huangguoai.com/video/123/',
    'https://huangguoai.com:8443/video/123/',
    'https://huangguoai.com/video/0/',
    'https://huangguoai.com/video/123/ep-0/',
    'https://huangguoai.com/video/123/ep-2/other/',
    'https://huangguoai.com/video/123/?ep=2',
    'https://huangguoai.com/video/123/?token=private',
    'https://huangguoai.com/video/123/#ep-2',
    'https://huangguoai.com/video/123/?',
    'https://huangguoai.com/video/123/#',
    'https://huangguoai.com/video/123/\n',
    'https://huangguoai.com/video/123/ep-02/',
    'https://huangguoai.com/video/123/../456/',
    'https://huangguoai.com/video/%31%32%33/',
    'https://huangguoai.com/video//123/',
    'https://huangguoai.com/',
])
def test_registry_and_remote_queue_reject_noncanonical_urls(url):
    assert sites.validate_url(url) is None
    with pytest.raises(ValueError):
        validate_job({'url': url})


def test_identity_uses_site_and_episode_not_jav_shaped_title_or_stored_code():
    for key in ('_code', 'code'):
        video = {'url': 'https://huangguoai.com/video/123/ep-2/',
                 'title': 'ABC-123 A drama title', key: 'abc-123'}
        assert video_code(video) == 'huangguo-123-ep-2'
    assert video_code({'url': 'https://huangguoai.com/video/123/',
                       '_site': 'JableTV', 'title': 'ABC-123'}) == 'huangguo-123-ep-1'


def test_identity_does_not_guess_from_invalid_huangguo_urls():
    assert video_code({'_site': 'Huangguo', 'url': 'https://huangguoai.com/video/123/?ep=2',
                       'title': 'ABC-123', '_code': 'abc-123'}) == ''


def test_episode_identity_preserves_each_episode_and_series():
    candidates = [
        {'url': url, 'title': 'ABC-123 Same dramatic title'} for url in (
            'https://huangguoai.com/video/123/',
            'https://www.huangguoai.com/video/123/ep-1/',
            'https://huangguoai.com/video/123/ep-2/',
            'https://huangguoai.com/video/456/ep-2/',
            'https://jable.tv/videos/abc-123/',
        )
    ]
    kept, decisions = dedupe_video_candidates(candidates, 'standard')
    assert [video_code(video) for video in kept] == [
        'huangguo-123-ep-1', 'huangguo-123-ep-2', 'huangguo-456-ep-2', 'abc-123']
    assert len(decisions) == 1


@pytest.mark.parametrize('host', ['huangguoai.com', 'www.huangguoai.com'])
def test_site_labels_and_display_code(host):
    url = f'https://{host}/video/123/ep-2/'
    assert site_from_url(url) == 'Huangguo'
    assert downloads.site_name(url) == 'Huangguo'
    assert downloads.video_code(url, 'ABC-123 Drama title') == 'huangguo-123-ep-2'


@pytest.mark.parametrize('host', ['evil.huangguoai.com', 'huangguoai.com.evil.test'])
def test_similar_hosts_are_not_huangguo(host):
    url = f'https://{host}/video/123/'
    assert site_from_url(url) != 'Huangguo'
    assert downloads.site_name(url) != 'Huangguo'


def test_site_language_does_not_imply_source_subtitles():
    video = {'_site': 'Huangguo', 'url': 'https://huangguoai.com/video/123/',
             'title': '中文短劇', '_target_id': 'category:chinese-subtitle',
             '_source_subtitle_evidence': 'supjav-category-chinese-subtitle'}
    assert trusted_chinese_subtitle_evidence(video) == ()
    assert video_versions({'url': video['url'], 'title': video['title']}) == {'standard'}


def test_cli_help_and_parser_advertise_huangguo_without_url_rewriting():
    parser = get_parser()
    assert 'Huangguo' in parser.format_help()
    url = 'https://huangguoai.com/video/123/ep-2/'
    assert parser.parse_args(['--nogui', '--url', url]).url == url


def test_web_queue_preserves_two_episode_jobs_on_reload(tmp_path):
    queue = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    urls = ['https://huangguoai.com/video/123/', 'https://huangguoai.com/video/123/ep-2/']
    queue.add([{'url': url} for url in urls])
    recovered = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    assert [job['url'] for job in recovered.snapshot()['jobs']] == urls


def test_factory_forwards_download_settings_without_touching_network(monkeypatch, tmp_path):
    url = 'https://huangguoai.com/video/123/ep-2/'
    adapter = sites.validate_url(url)
    assert adapter is not None
    calls = []

    def construct(self, source, **settings):
        calls.append((source, settings))

    monkeypatch.setattr(adapter, '__init__', construct)
    instance = sites.CreateSite(url, str(tmp_path), silence=True, max_workers=3,
                                resolution='720', filename_template='{code}')
    assert isinstance(instance, adapter)
    assert calls == [(url, {
        'savepath': str(tmp_path), 'silence': True, 'max_workers': 3,
        'resolution': '720', 'filename_template': '{code}',
    })]
