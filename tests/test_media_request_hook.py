"""Site-specific media transport must cover every resource, not just manifests."""
from types import SimpleNamespace
import threading

import pytest

from uav_downloader.sites import base


def test_huangguo_template_uses_episode_identity_not_drama_title():
    assert base._apply_filename_template(
        'Story 2049', 'huangguo-42-ep-2',
        'https://huangguoai.com/video/42/ep-2/', '{site}_{code}') == (
            'Huangguo_huangguo-42-ep-2')


def test_default_media_get_preserves_legacy_transport(monkeypatch):
    crawler = base.M3U8Crawler.__new__(base.M3U8Crawler)
    crawler._extra_headers = {'Referer': 'https://fixture.example/'}
    calls = []
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: calls.append((a, kw)))
    crawler._media_get('https://cdn.example/master.m3u8', timeout=7)
    assert calls == [(('https://cdn.example/master.m3u8', crawler._m3u8_headers()), {'timeout': 7})]


@pytest.mark.parametrize('kind', ['manifest', 'key', 'segment', 'image'])
def test_resource_requests_use_site_hook(monkeypatch, tmp_path, kind):
    crawler = base.M3U8Crawler.__new__(base.M3U8Crawler)
    crawler._extra_headers = {}
    crawler._m3u8url = 'https://cdn.example/master.m3u8'
    crawler._imageUrl = 'https://cdn.example/cover.jpg'
    crawler._dest_folder = crawler._temp_folder = str(tmp_path)
    crawler._targetName = 'fixture'
    crawler._cancel_job = False
    crawler._speed_lock = threading.Lock()
    crawler._pending_set = set()
    calls = []

    def hook(url, timeout=20):
        calls.append((url, timeout))
        return SimpleNamespace(status_code=404, content=b'', headers={}, text='')

    def forbidden(*a, **kw):
        raise AssertionError('Resource bypassed site transport hook')

    crawler._media_get = hook
    monkeypatch.setattr(base, '_http_get', forbidden)
    if kind == 'manifest':
        with pytest.raises(Exception, match='404'):
            crawler._load_m3u8(crawler._m3u8url)
        assert calls == [(crawler._m3u8url, 20)]
    elif kind == 'key':
        crawler._load_m3u8 = lambda url: SimpleNamespace(
            playlists=[], keys=[SimpleNamespace(uri='key.bin')])
        with pytest.raises(Exception, match='AES'):
            crawler._create_m3u8()
        assert calls == [('https://cdn.example/key.bin', 15)]
    elif kind == 'segment':
        assert crawler._scrape((0, 'https://cdn.example/seg.ts')) is False
        assert calls == [('https://cdn.example/seg.ts', 25)]
    else:
        assert crawler.download_image() is None
        assert calls == [(crawler._imageUrl, 15)]
