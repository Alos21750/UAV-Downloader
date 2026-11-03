"""Offline FST regressions: source choice must match the embedded player."""
import json
from types import SimpleNamespace

import pytest

from uav_downloader.sites import supjav


def _player(links, order=('hls4', 'hls3', 'hls2'), media_type='hls', packed=True):
    expression = ' || '.join('links.' + key for key in order)
    code = ('var links=' + json.dumps(links) + ';jwplayer("vplayer").setup({'
            'sources:[{file:' + expression + ',type:"' + media_type + '"}]});')
    if packed:
        # Exercise the real static unpacker, not eval or a monkeypatched decoder.
        code = "eval(function(p,a,c,k,e,d){return p;}('" + code + "',36,0,''.split('|'),0,{}))"
    return '<script>' + code + '</script>'


def _resolve(body, embed_url='https://fc2stream.example/e/video', name='FST'):
    class Scraper:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200, text=body, url=embed_url)
    return supjav.SiteSupJav.__new__(supjav.SiteSupJav)._resolve_server(Scraper(), name, 'abc')


def test_fst_relative_primary_precedes_absolute_lower_quality_mirror():
    body = _player({'hls4': '/stream/high/master.m3u8',
                    'hls3': 'https://cdn.example/high/master.txt',
                    'hls2': 'https://cdn.example/low/master.m3u8'})
    url, headers = _resolve(body)
    assert url == 'https://fc2stream.example/stream/high/master.m3u8'
    assert headers == {'Referer': 'https://fc2stream.example/e/video',
                       'Origin': 'https://fc2stream.example'}


@pytest.mark.parametrize('path', ['master.txt', 'manifest?token=abc', 'master.m3u8'])
def test_fst_hls_typed_secondary_does_not_require_m3u8_suffix(path):
    url, _ = _resolve(_player({'hls4': '', 'hls3': 'https://cdn.example/' + path,
                              'hls2': 'https://cdn.example/low/master.m3u8'}))
    assert url == 'https://cdn.example/' + path


@pytest.mark.parametrize('packed', [True, False])
def test_fst_uses_declared_player_order_not_dictionary_order(packed):
    body = _player({'hls2': 'https://cdn.example/low/master.m3u8',
                    'hls4': '/high/master.m3u8', 'hls3': '/chosen/master.m3u8'},
                   order=('hls3', 'hls4', 'hls2'), packed=packed)
    assert _resolve(body)[0] == 'https://fc2stream.example/chosen/master.m3u8'


def test_fst_relative_source_uses_final_embed_url_not_redirector():
    body = _player({'hls4': '../stream/master.m3u8?token=a%2Fb&x=1'})
    assert _resolve(body, 'https://final.example:8443/embed/video')[0] == (
        'https://final.example:8443/stream/master.m3u8?token=a%2Fb&x=1')


def test_fst_uses_final_host_after_http_redirects():
    requested = []

    class Scraper:
        def get(self, url, **kwargs):
            requested.append(url)
            if len(requested) == 1:
                return SimpleNamespace(status_code=302, url=url,
                                       headers={'Location': 'https://final.example/e/video'},
                                       close=lambda: None)
            return SimpleNamespace(status_code=200, url=url,
                                   text=_player({'hls4': '/stream/master.m3u8'}))

    url, headers = supjav.SiteSupJav.__new__(supjav.SiteSupJav)._resolve_server(
        Scraper(), 'FST', 'abc')
    assert len(requested) == 2
    assert url == 'https://final.example/stream/master.m3u8'
    assert headers == {'Referer': 'https://final.example/e/video', 'Origin': 'https://final.example'}


def test_fst_decodes_real_packer_dictionary_tokens():
    body = _player({'hls4': '/stream/master.m3u8'})
    body = body.replace('var links=', 'var 0=').replace('links.hls', '0.hls')
    body = body.replace(",36,0,''.split('|')", ",36,1,'links'.split('|')")
    assert _resolve(body)[0] == 'https://fc2stream.example/stream/master.m3u8'


def test_fst_accepts_quoted_properties_and_type_before_file():
    body = _player({'hls4': '//cdn.example/manifest'}, packed=False)
    body = body.replace('file:links.hls4 || links.hls3 || links.hls2,type:"hls"',
                        '"type":"hls", "file":links.hls4')
    assert _resolve(body)[0] == 'https://cdn.example/manifest'


@pytest.mark.parametrize('unsafe', [
    'http://cdn.example/master.m3u8', 'file:///tmp/master.m3u8',
    'https://localhost/master.m3u8', 'https://127.0.0.1/master.m3u8',
    '//192.168.1.1/master.m3u8', 'https://user:password@cdn.example/master.m3u8',
    'https://[::1]/master.m3u8', 'https://cdn.example:bad/master.m3u8',
])
def test_fst_rejects_unsafe_primary_and_uses_safe_hls_alternative(unsafe):
    body = _player({'hls4': unsafe, 'hls3': 'https://cdn.example/manifest', 'hls2': ''})
    assert _resolve(body)[0] == 'https://cdn.example/manifest'


@pytest.mark.parametrize('value', [None, False, 4, [], {}, ''])
def test_fst_ignores_non_string_or_empty_link_values(value):
    body = _player({'hls4': value, 'hls3': '/stream/master.m3u8'})
    assert _resolve(body)[0] == 'https://fc2stream.example/stream/master.m3u8'


def test_fst_extensionless_source_requires_hls_type():
    assert _resolve(_player({'hls4': 'https://cdn.example/manifest'}, media_type='mp4'))[0] is None


def test_fst_legacy_absolute_m3u8_still_supported():
    body = _player({'hls2': 'https://cdn.example/legacy/master.m3u8'}, order=('hls2',))
    assert _resolve(body)[0] == 'https://cdn.example/legacy/master.m3u8'


@pytest.mark.parametrize('name', ['EVS', 'LUC'])
def test_fst_logic_does_not_change_other_packed_providers(name):
    body = _player({'hls4': '/stream/master.m3u8', 'hls2': 'https://cdn.example/master.m3u8'})
    assert _resolve(body, name=name)[0] == 'https://cdn.example/master.m3u8'


def test_fst_json_string_braces_are_not_object_terminators():
    body = _player({'hls4': '/stream/master.m3u8?token={signed}'})
    assert _resolve(body)[0] == 'https://fc2stream.example/stream/master.m3u8?token={signed}'


@pytest.mark.parametrize('body', [
    '<script>var links={"hls4":fetch("https://cdn.example/manifest")};</script>',
    '<script>var links=[];jwplayer("v").setup({sources:[{file:links.hls4,type:"hls"}]});</script>',
    '<script>var links={broken};</script>',
])
def test_fst_malformed_or_dynamic_data_is_not_executed(body):
    assert _resolve(body)[0] is None


def test_fst_conditional_expression_is_not_mistaken_for_static_or_chain():
    body = _player({'hls4': 'https://cdn.example/manifest'}, packed=False)
    body = body.replace('file:links.hls4 || links.hls3 || links.hls2',
                        'file:links.hls4 || fetch("https://other.example/source")')
    assert _resolve(body)[0] is None
