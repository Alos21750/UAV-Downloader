import os
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
    mod.create_scraper = lambda *args, **kwargs: None
    return mod


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
    return mod


_stub_runtime_dependency('cloudscraper', _cloudscraper_stub)
_stub_runtime_dependency('m3u8', _m3u8_stub)

from uav_downloader.sites.base import M3U8Crawler, _truncate_target_name


_TITLES = {}


class DummyCrawler(M3U8Crawler):
    website_pattern = r'https://example\.test/.+/$'
    website_dirname_pattern = r'https://example\.test/([^/]+)/$'

    def get_url_infos(self):
        self._targetName = _TITLES[self._dirName]
        self._imageUrl = None
        self._m3u8url = 'https://cdn.example.test/master.m3u8'


def _make_site(dest, dirname, title):
    _TITLES[dirname] = title
    return DummyCrawler(f'https://example.test/{dirname}/', savepath=str(dest), silence=True)


def test_long_target_name_is_capped_with_uid_suffix(tmp_path):
    dest = tmp_path / ('normal_dest_' + 'x' * 40)
    title = 'MIDA-644 ' + 'A' * (240 - len('MIDA-644 '))

    site = _make_site(dest, 'uid12345', title)
    final_part = os.path.join(site.dest_folder(), site.target_name() + '.mp4.part')

    assert len(final_part) <= 255
    assert site.target_name().startswith('MIDA-644')
    assert site.target_name().endswith('_uid12345')


def test_long_titles_with_same_prefix_get_different_filenames(tmp_path):
    dest = tmp_path / ('normal_dest_' + 'x' * 40)
    prefix = 'ABCD-001 ' + 'B' * 200

    site1 = _make_site(dest, 'aaaa1111', prefix + ' first title tail')
    site2 = _make_site(dest, 'bbbb2222', prefix + ' second title tail')

    assert site1.target_name() != site2.target_name()
    assert site1.target_name().endswith('_aaaa1111')
    assert site2.target_name().endswith('_bbbb2222')


def test_safe_title_punctuation_is_preserved(tmp_path):
    title = 'FNS-235 我早上醒来就看到一个美人……无法抗拒！“真的吗？”】'

    site = _make_site(tmp_path, 'punctuation123', title)

    assert site.target_name() == title


def test_windows_forbidden_punctuation_uses_fullwidth_equivalents(tmp_path):
    site = _make_site(tmp_path, 'symbols123', 'A:B/C\\D?E*F"G<H>I|')

    assert site.target_name() == 'A：B／C＼D？E＊F＂G＜H＞I｜'


def test_html_entities_are_unescaped_before_filename_sanitizing(tmp_path):
    site = _make_site(tmp_path, 'entities123', 'A &amp; B &#39;quote&#39;')

    assert site.target_name() == "A & B 'quote'"


def test_control_only_title_falls_back_to_dirname(tmp_path):
    site = _make_site(tmp_path, 'fallback123', '\x00\x01\x02')

    assert site.target_name() == 'fallback123'


def test_control_only_title_sanitizes_reserved_dirname(tmp_path):
    site = _make_site(tmp_path, 'CON', '\x00\x01\x02')

    assert site.target_name() == '_CON'


def test_trailing_ascii_dots_remain_visible_and_windows_safe(tmp_path):
    site = _make_site(tmp_path, 'dots123', 'ABCD-001 Wait...')

    assert site.target_name() == 'ABCD-001 Wait．．．'


def test_windows_reserved_basename_is_prefixed_and_creatable(tmp_path):
    site = _make_site(tmp_path, 'reserved123', 'CON.txt')

    assert site.target_name() == '_CON.txt'
    with open(site._get_video_savename(), 'wb') as output:
        output.write(b'')


def test_long_emoji_title_respects_windows_utf16_path_limit(tmp_path):
    site = _make_site(tmp_path, 'emoji123', 'ABCD-001 ' + '😀' * 240)
    final_part = os.path.join(site.dest_folder(), site.target_name() + '.mp4.part')

    assert len(final_part.encode('utf-16-le')) // 2 <= 255
    assert site.target_name().endswith('_emoji123')
    with open(final_part, 'wb') as output:
        output.write(b'')


def test_long_cjk_title_respects_posix_component_byte_limit(tmp_path):
    site = _make_site(tmp_path, 'cjk12345', 'ABCD-001 ' + '測' * 240)
    filename = site.target_name() + '.mp4.part'

    assert len(filename.encode('utf-8')) <= 255
    assert site.target_name().endswith('_cjk12345')


def test_too_long_destination_never_drops_reserved_name_prefix():
    dest = 'C:\\' + 'x' * 239

    with pytest.raises(OSError, match='Destination path is too long'):
        _truncate_target_name('A' * 240, dest, 'CON')


def test_dirname_is_capped_for_temp_folder(tmp_path):
    dirname = 'x' * 100
    site = _make_site(tmp_path, dirname, 'ABCD-001 title')

    assert len(site._dirName) == 80
    # Full resource identity, rather than the truncated display ID, bounds
    # cache paths and distinguishes IDs sharing the same 80-character prefix.
    other = _make_site(tmp_path, 'x' * 150 + 'different', 'same title')
    assert site._temp_folder != other._temp_folder
    segment = site._seg_savename(0)
    assert len(segment.encode('utf-16-le')) // 2 <= 255
    assert os.path.commonpath([site._temp_folder, site.dest_folder()]) == site.dest_folder()
