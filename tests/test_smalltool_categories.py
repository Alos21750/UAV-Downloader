import sys
import types
from urllib.parse import unquote


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

from uav_downloader.apps.watch import SmallToolWorker
from uav_downloader.apps.watch_categories import SITES, find_target, iter_targets, selection_key


def test_smalltool_registry_has_complete_grouped_targets():
    assert len(list(iter_targets('JableTV'))) == 129
    assert [len(g['targets']) for g in SITES['JableTV']['groups']] == [
        6, 12, 17, 13, 11, 19, 16, 16, 13, 6]
    assert len(list(iter_targets('MissAV'))) == 102
    assert [len(g['targets']) for g in SITES['MissAV']['groups']] == [7, 22, 37, 36]
    assert len(list(iter_targets('SupJav'))) == 10
    assert [len(g['targets']) for g in SITES['SupJav']['groups']] == [4, 6]
    assert len(list(iter_targets('Hanime1'))) == 272
    assert [len(g['targets']) for g in SITES['Hanime1']['groups']] == [
        9, 9, 6, 8, 9, 14, 19, 23, 21, 27, 25, 46, 56]

    hanime_names = {target['name'] for target in iter_targets('Hanime1')}
    assert {'裏番', '泡麵番', '過去 24 小時', '0 - 10 分鐘',
            '同人作品', 'NTR', 'BDSM', 'POV'} <= hanime_names


def test_target_ids_are_unique_and_legacy_names_still_resolve():
    for site_name in SITES:
        targets = list(iter_targets(site_name))
        ids = [target['id'] for target in targets]
        assert len(ids) == len(set(ids))
        assert all(target['url'].startswith('https://') for target in targets)

    old = find_target('JableTV', legacy_name='中文字幕')
    assert old['id'] == 'category:chinese-subtitle'
    assert selection_key('JableTV', old['id']) == (
        'JableTV|category:chinese-subtitle')

    incest = find_target('MissAV', legacy_name='亂倫')
    assert '/genres/乱伦' in unquote(incest['url'])
    assert '/genres/亂倫' not in unquote(incest['url'])

    exclusive = find_target('MissAV', legacy_name='獨家')
    assert '/genres/独家' in unquote(exclusive['url'])


def test_smalltool_uses_site_specific_pagination_and_supjav_dates():
    worker = SmallToolWorker.__new__(SmallToolWorker)
    assert worker._build_page_url(
        'SupJav', 'https://supjav.com/category/chinese-subtitles', 2
    ) == 'https://supjav.com/category/chinese-subtitles/page/2'
    assert worker._build_page_url(
        'SupJav', 'https://supjav.com/popular?sort=week', 2
    ) == 'https://supjav.com/popular?sort=week&page=2'
    assert worker._parse_supjav_listing_date('2026/07/12').isoformat() == (
        '2026-07-12T00:00:00+00:00')
    assert worker._parse_supjav_listing_date('') is None
    assert worker._build_page_url(
        'Hanime1', 'https://hanime1.me/search?genre=3DCG', 2
    ) == 'https://hanime1.me/search?genre=3DCG&page=2'
    assert worker._parse_relative_date('16小時前').isoformat().endswith('+00:00')
