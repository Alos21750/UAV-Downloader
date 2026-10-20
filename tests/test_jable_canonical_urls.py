"""A Jable video identifier becomes a directory name, never a path (#47 review)."""
import pytest

from uav_downloader import sites
from uav_downloader.sites.jabletv import SiteJableTV, SiteJableTV_Backup


@pytest.mark.parametrize(('adapter', 'host'), [
    (SiteJableTV, 'jable.tv'),
    (SiteJableTV_Backup, 'fs1.app'),
])
@pytest.mark.parametrize('slug', [
    '..', '../escape', '../../escape', '/../../escape',
    r'..\escape', '%2e%2e', '%2E%2E%2Fescape', 'abc%2fescape',
    'abc%5cescape', 'abc/def', 'abc?token=hidden', 'abc#fragment',
])
def test_jable_video_identifier_rejects_path_and_query_syntax(adapter, host, slug):
    assert adapter.validate_url(f'https://{host}/videos/{slug}/') is None


@pytest.mark.parametrize('url', [
    'https://jable.tv/videos/abc-123/\n',
    'https://jable.tv/videos/abc-123/?next=/',
    'https://jable.tv/videos/abc-123/#/',
    'http://jable.tv/videos/abc-123/',
    'https://jable.tv:443/videos/abc-123/',
    'https://user:pass@jable.tv/videos/abc-123/',
    'https://jable.tv.evil.invalid/videos/abc-123/',
])
def test_jable_video_urls_are_canonical_and_fully_anchored(url):
    assert sites.validate_url(url) is None


@pytest.mark.parametrize(('adapter', 'host'), [
    (SiteJableTV, 'jable.tv'),
    (SiteJableTV_Backup, 'fs1.app'),
])
@pytest.mark.parametrize('slug', ['abc-123', 'FC2-PPV-1234567', 'h_123_abc', '12345'])
def test_jable_single_component_video_slugs_remain_supported(adapter, host, slug):
    assert adapter.validate_url(f'https://{host}/videos/{slug}/') == slug


def test_jable_traversal_is_rejected_before_metadata_or_file_access(monkeypatch, tmp_path):
    monkeypatch.setattr(SiteJableTV, 'get_url_infos',
                        lambda self: pytest.fail('must reject before network access'))
    root = tmp_path / 'downloads'
    assert sites.CreateSite('https://jable.tv/videos/../../escape/', str(root)) is None
    assert not root.exists()
    assert not (tmp_path.parent / 'escape').exists()
