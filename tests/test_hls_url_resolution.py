"""Resolve HLS children from the final playlist location, never string joins."""
from types import SimpleNamespace

import pytest

from uav_downloader.sites.base import M3U8Crawler


def _response(url, text='', content=None):
    return SimpleNamespace(url=url, status_code=200, text=text,
                           content=text.encode() if content is None else content,
                           headers={})


@pytest.mark.parametrize('key_uri,segment_uri,expected_key,expected_segment', [
    ('/keys/a.key', '/segments/a.ts', 'https://cdn.example/keys/a.key',
     'https://cdn.example/segments/a.ts'),
    ('//keys.example/a.key', '//media.example/a.ts', 'https://keys.example/a.key',
     'https://media.example/a.ts'),
    ('../keys/a.key', '../segments/a.ts', 'https://cdn.example/keys/a.key',
     'https://cdn.example/segments/a.ts'),
    ('a.key', 'a.ts?token=1%2F2', 'https://cdn.example/nested/a.key',
     'https://cdn.example/nested/a.ts?token=1%2F2'),
    ('https://keys.example/a.key', 'https://media.example/a.ts',
     'https://keys.example/a.key', 'https://media.example/a.ts'),
])
def test_encrypted_playlist_relative_key_and_segment_urls(
        key_uri, segment_uri, expected_key, expected_segment):
    crawler = M3U8Crawler.__new__(M3U8Crawler)
    crawler._m3u8url = 'https://cdn.example/nested/media.m3u8?signature=abc'
    calls = []
    body = ('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:7\n'
            f'#EXT-X-KEY:METHOD=AES-128,URI="{key_uri}"\n'
            f'#EXTINF:2,\n{segment_uri}\n#EXT-X-ENDLIST\n')

    def get(url, timeout=20):
        calls.append(url)
        if url == crawler._m3u8url:
            return _response(url, body)
        assert url == expected_key
        return _response(url, content=b'0123456789abcdef')

    crawler._media_get = get
    crawler._create_m3u8()
    assert calls == [crawler._m3u8url, expected_key]
    assert crawler._tsList == [expected_segment]
    assert crawler._key_content == b'0123456789abcdef'
    assert crawler._key_method == 'AES-128'
    assert crawler._media_sequence == 7
    assert crawler._make_cipher(0).iv == (7).to_bytes(16, 'big')


@pytest.mark.parametrize('variant_uri,expected_variant', [
    ('high/media.m3u8', 'https://redirect.example/master/high/media.m3u8'),
    ('/high/media.m3u8', 'https://redirect.example/high/media.m3u8'),
    ('//variant.example/high/media.m3u8', 'https://variant.example/high/media.m3u8'),
    ('https://variant.example/high/media.m3u8', 'https://variant.example/high/media.m3u8'),
])
def test_master_and_variant_redirects_control_all_relative_children(variant_uri, expected_variant):
    crawler = M3U8Crawler.__new__(M3U8Crawler)
    crawler._m3u8url = 'https://original.example/start.m3u8'
    crawler._resolution_pref = 'highest'
    calls = []

    def get(url, timeout=20):
        calls.append(url)
        if url == crawler._m3u8url:
            return _response('https://redirect.example/master/index.m3u8',
                             '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1920x1080\n'
                             + variant_uri + '\n')
        if url == expected_variant:
            return _response('https://final.example/media/playlist.m3u8',
                             '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="keys/a.key"\n'
                             '#EXTINF:2,\nsegments/a.ts\n#EXT-X-ENDLIST\n')
        assert url == 'https://final.example/media/keys/a.key'
        return _response(url, content=b'0123456789abcdef')

    crawler._media_get = get
    crawler._create_m3u8()
    assert calls == [crawler._m3u8url, expected_variant, 'https://final.example/media/keys/a.key']
    assert crawler._tsList == ['https://final.example/media/segments/a.ts']


def test_direct_media_redirect_also_controls_segment_base():
    crawler = M3U8Crawler.__new__(M3U8Crawler)
    crawler._m3u8url = 'https://original.example/media.m3u8'
    crawler._media_get = lambda *a, **kw: _response(
        'https://final.example/nested/media.m3u8',
        '#EXTM3U\n#EXTINF:2,\na.ts\n#EXT-X-ENDLIST\n')
    crawler._create_m3u8()
    assert crawler._tsList == ['https://final.example/nested/a.ts']


def test_query_only_resources_preserve_redirected_playlist_filename():
    crawler = M3U8Crawler.__new__(M3U8Crawler)
    crawler._m3u8url = 'https://original.example/start.m3u8'
    crawler._resolution_pref = 'highest'
    calls = []

    def get(url, timeout=20):
        calls.append(url)
        if len(calls) == 1:
            return _response('https://redirect.example/master.m3u8?old=1',
                             '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\n?variant=1\n')
        if len(calls) == 2:
            assert url == 'https://redirect.example/master.m3u8?variant=1'
            return _response('https://final.example/media.m3u8?old=2',
                             '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="?key=1"\n'
                             '#EXTINF:2,\n?segment=7\n#EXT-X-ENDLIST\n')
        assert url == 'https://final.example/media.m3u8?key=1'
        return _response(url, content=b'0123456789abcdef')

    crawler._media_get = get
    crawler._create_m3u8()
    assert crawler._tsList == ['https://final.example/media.m3u8?segment=7']


def test_child_paths_do_not_inherit_parent_signed_query():
    crawler = M3U8Crawler.__new__(M3U8Crawler)
    crawler._m3u8url = 'https://original.example/start.m3u8'
    crawler._media_get = lambda *a, **kw: _response(
        'https://final.example/nested/media.m3u8?private=parent',
        '#EXTM3U\n#EXTINF:2,\nnext.ts\n#EXTINF:2,\n//other.example/next.ts\n#EXT-X-ENDLIST\n')
    crawler._create_m3u8()
    assert crawler._tsList == ['https://final.example/nested/next.ts',
                               'https://other.example/next.ts']
