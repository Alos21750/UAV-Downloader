import re
import sys
import types

import pytest


def _stub_runtime_dependency(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


def _cloudscraper_stub():
    mod = types.ModuleType('cloudscraper')

    def create_scraper(*args, **kwargs):
        raise AssertionError('cloudscraper should not be used by offline tests')

    mod.create_scraper = create_scraper
    return mod


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
    return mod


_stub_runtime_dependency('cloudscraper', _cloudscraper_stub)
_stub_runtime_dependency('m3u8', _m3u8_stub)

from uav_downloader.core import config
from uav_downloader.i18n import locales
from uav_downloader.sites.missav import MissAVBrowser


LANGS = ['en', 'th', 'zh', 'zh-Hans', 'ja']
PLACEHOLDER_RE = re.compile(r'\{(\w+)\}')


@pytest.fixture(autouse=True)
def _reset_locale():
    yield
    locales.set_lang('en')


def _placeholders(value):
    return set(PLACEHOLDER_RE.findall(value))


def test_locale_key_parity():
    keys = set(locales.STRINGS['en'])
    for lang in LANGS:
        assert set(locales.STRINGS[lang]) == keys


def test_locale_placeholder_parity():
    for key in locales.STRINGS['en']:
        expected = _placeholders(locales.STRINGS['en'][key])
        for lang in LANGS:
            assert _placeholders(locales.STRINGS[lang][key]) == expected, key


def test_add_to_queue_locale_key_present():
    expected = {
        'en': 'Add to Queue',
        'th': 'เพิ่มเข้า Queue',
        'zh': '加入清單',
        'zh-Hans': '加入清单',
        'ja': 'キューに追加',
    }
    for lang, text in expected.items():
        assert locales.STRINGS[lang]['add_to_queue'] == text


def test_set_lang_accepts_supported_and_falls_back_to_english():
    locales.set_lang('ja')
    assert locales.get_lang() == 'ja'

    locales.set_lang('xx')
    assert locales.get_lang() == 'en'


def test_ui_font_tracks_current_language():
    locales.set_lang('zh')
    assert locales.ui_font() == 'Microsoft JhengHei'

    locales.set_lang('ja')
    assert locales.ui_font() == 'Yu Gothic UI'

    locales.set_lang('en')
    assert locales.ui_font() == 'Microsoft JhengHei'


def test_state_label_translates_known_codes_and_keeps_unknown_codes():
    locales.set_lang('en')
    assert locales.state_label('下載中') == 'Downloading'

    locales.set_lang('ja')
    assert locales.state_label('已下載') == '完了'
    assert locales.state_label('未知狀態') == '未知狀態'


def test_recognition_quality_and_no_speech_copy_is_complete():
    required = {
        'recognition_quality_setting',
        'recognition_quality_auto',
        'recognition_quality_quality',
        'recognition_quality_balanced',
        'recognition_quality_fast',
        'recognition_quality_desc',
        'subtitle_no_speech',
        'subtitle_empty_result',
    }
    for lang in LANGS:
        strings = locales.STRINGS[lang]
        assert required <= strings.keys()
        assert 'ReazonSpeech' in strings['recognition_quality_desc']
        assert '178' in strings['recognition_quality_desc']
        assert 'large-v3-turbo' in strings['recognition_quality_desc']
        assert '574' in strings['recognition_quality_desc']
        assert '190' in strings['recognition_quality_desc']
        assert '60' in strings['recognition_quality_desc']
        assert '147' in strings['translation_provider_hint']
        assert '147' in strings['subtitle_desc']

    locales.set_lang('zh')
    assert locales.T('subtitle_no_speech') == '未偵測到日語語音'
    assert locales.state_label('未偵測到日語語音') == '未偵測到日語語音'


def test_site_language_codes():
    expected = {
        'en': ('en', ''),
        'zh': ('', 'zh'),
        'zh-Hans': ('cn', 'zh'),
        'ja': ('ja', 'ja'),
        'th': ('th', ''),
    }
    for lang, (missav_lang, supjav_lang) in expected.items():
        locales.set_lang(lang)
        assert locales.T('missav_lang') == missav_lang
        assert locales.T('supjav_lang') == supjav_lang


def test_config_prefs_keep_theme_and_language(tmp_path, monkeypatch):
    path = tmp_path / 'ui_prefs.json'
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(path))

    config.set_theme('dark')
    config.set_ui_lang('ja')

    assert config.get_theme() == 'dark'
    assert config.get_ui_lang() == 'ja'

    config.set_ui_lang('th')
    assert config.get_ui_lang() == 'th'

    config.set_theme('light')

    assert config.get_theme() == 'light'
    assert config.get_ui_lang() == 'th'


def test_missav_language_paths_use_empty_string_for_default(monkeypatch):
    default_cats = MissAVBrowser.fetch_categories()
    cn_cats = MissAVBrowser.fetch_categories(lang='cn')

    assert default_cats[0]['url'] == 'https://missav.ai/dm298/today-hot'
    assert cn_cats[0]['url'] == 'https://missav.ai/dm298/cn/today-hot'

    called = []
    monkeypatch.setattr(
        MissAVBrowser,
        'fetch_page',
        classmethod(lambda cls, url: called.append(url) or []),
    )

    MissAVBrowser.search('abc def')
    MissAVBrowser.search('abc def', lang='cn')

    assert called == [
        'https://missav.ai/search/abc%20def',
        'https://missav.ai/cn/search/abc%20def',
    ]
