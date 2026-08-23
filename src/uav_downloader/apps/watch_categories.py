#!/usr/bin/env python
# coding: utf-8
"""Grouped, stable-ID category registry for UAV Watcher."""

from urllib.parse import quote

from uav_downloader.i18n import sites as site_i18n
from uav_downloader.i18n.locales import T
from uav_downloader.sites.jabletv import JableTVBrowser
from uav_downloader.sites.missav import MissAVBrowser
from uav_downloader.sites.supjav import SupJavBrowser
from uav_downloader.sites.hanime1 import (
    HANIME1_FILTER_DATES,
    HANIME1_FILTER_DURATIONS,
    HANIME1_FILTER_TAG_GROUPS,
    Hanime1Browser,
)


def _target(target_id, name, url, **extra):
    return {'id': target_id, 'name': name, 'url': url, **extra}


def _group(group_id, name, targets, **extra):
    return {'id': group_id, 'name': name, 'targets': targets, **extra}


JABLE_GROUPS = [
    _group('feeds', 'Feeds', [
        _target('feed:latest', '最近更新', 'https://jable.tv/latest-updates/'),
        _target('feed:new', '新片上架', 'https://jable.tv/new-release/'),
        _target('hot:all', '熱門總榜', 'https://jable.tv/hot/'),
        _target('hot:today', '今日熱門', 'https://jable.tv/hot/?t=today'),
        _target('hot:week', '本週熱門', 'https://jable.tv/hot/?t=week'),
        _target('hot:month', '本月熱門', 'https://jable.tv/hot/?t=month'),
    ]),
    _group('categories', 'Categories', [
        _target('category:bdsm', 'BDSM', 'https://jable.tv/categories/bdsm/'),
        _target('category:sex-only', 'Sex Only', 'https://jable.tv/categories/sex-only/'),
        _target('category:chinese-subtitle', '中文字幕',
                'https://jable.tv/categories/chinese-subtitle/'),
        _target('category:insult', '凌辱', 'https://jable.tv/categories/insult/'),
        _target('category:uniform', '制服', 'https://jable.tv/categories/uniform/'),
        _target('category:roleplay', '角色扮演', 'https://jable.tv/categories/roleplay/'),
        _target('category:private-cam', '偷拍', 'https://jable.tv/categories/private-cam/'),
        _target('category:uncensored', '無碼', 'https://jable.tv/categories/uncensored/'),
        _target('category:pov', 'POV', 'https://jable.tv/categories/pov/'),
        _target('category:groupsex', '多人', 'https://jable.tv/categories/groupsex/'),
        _target('category:pantyhose', '絲襪', 'https://jable.tv/categories/pantyhose/'),
        _target('category:lesbian', '女同性戀', 'https://jable.tv/categories/lesbian/'),
    ]),
]

for _tag_group_name, _tags in JableTVBrowser.SIDEBAR_TAGS.items():
    JABLE_GROUPS.append(_group(
        f'tags:{_tag_group_name}',
        _tag_group_name,
        [_target(f'tag:{slug}', name, JableTVBrowser.tag_url(slug), tag_slug=slug)
         for name, slug in _tags],
        tag_group=True,
    ))


MISSAV_FEEDS = [
    _target('feed:today-hot', '今日熱門', 'https://missav.ai/dm298/today-hot'),
    _target('feed:weekly-hot', '本週熱門', 'https://missav.ai/dm170/weekly-hot'),
    _target('feed:monthly-hot', '本月熱門', 'https://missav.ai/dm270/monthly-hot'),
    _target('feed:chinese-subtitle', '中文字幕', 'https://missav.ai/dm278/chinese-subtitle'),
    _target('feed:latest', '最近更新', 'https://missav.ai/dm539/new'),
    _target('feed:release', '新作上市', 'https://missav.ai/dm634/release'),
    _target('feed:uncensored-leak', '無碼流出', 'https://missav.ai/dm817/uncensored-leak'),
]

_MISSAV_PROVIDER_ROWS = [
    ('SIRO', 'https://missav.ai/dm36/siro'),
    ('LUXU', 'https://missav.ai/dm34/luxu'),
    ('GANA', 'https://missav.ai/dm34/gana'),
    ('PRESTIGE PREMIUM', 'https://missav.ai/dm1002/maan'),
    ('S-CUTE', 'https://missav.ai/dm38/scute'),
    ('ARA', 'https://missav.ai/dm34/ara'),
    ('FC2', 'https://missav.ai/dm541/fc2'),
    ('HEYZO', 'https://missav.ai/dm2097925/heyzo'),
    ('Tokyo Hot', 'https://missav.ai/dm42/tokyohot'),
    ('1Pondo', 'https://missav.ai/dm4854130/1pondo'),
    ('Caribbeancom', 'https://missav.ai/dm7502171/caribbeancom'),
    ('Caribbeancompr', 'https://missav.ai/dm88271/caribbeancompr'),
    ('10musume', 'https://missav.ai/dm6794110/10musume'),
    ('pacopacomama', 'https://missav.ai/dm2660747/pacopacomama'),
    ('Gachinco', 'https://missav.ai/dm150/gachinco'),
    ('XXX-AV', 'https://missav.ai/dm42/xxxav'),
    ('人妻斬', 'https://missav.ai/dm37/marriedslash'),
    ('頑皮 4610', 'https://missav.ai/dm33/naughty4610'),
    ('頑皮 0930', 'https://missav.ai/dm37/naughty0930'),
    ('麻豆傳媒', 'https://missav.ai/dm63/madou'),
    ('TWAV', 'https://missav.ai/dm31/twav'),
    ('Furuke', 'https://missav.ai/dm15/furuke'),
]
MISSAV_PROVIDERS = [
    _target(f'provider:{url.rstrip("/").rsplit("/", 1)[-1]}', name, url)
    for name, url in _MISSAV_PROVIDER_ROWS
]

_MISSAV_GENRE_ROWS = [
    ('VR', None), ('高清', 96), ('獨家', 142), ('中出', 132), ('單體作品', 122),
    ('巨乳', 141), ('人妻', 79), ('熟女', 123), ('素人', 149), ('美少女', 437),
    ('口交', 1302), ('多人運動', 321), ('騎乘', 487), ('薄格', 76), ('痴女', 324),
    ('4小時以上', 748), ('女高中生', 4454), ('潮吹', 163), ('苗條', 757),
    ('自拍', 978), ('合集', 788), ('乳交', 597), ('美乳', 216), ('戀物癖', 117),
    ('NTR', 757), ('企劃', 346), ('亂倫', 57), ('搭訕', 343), ('顏射', 319),
    ('淫亂', 904), ('偷拍', 523), ('4K', 55), ('劇情', 102), ('自慰', 8278),
    ('手淫', 95), ('姐姐', 794), ('羞辱', 162),
]

# MissAV displays Traditional Chinese in the default UI, but most taxonomy
# route slugs are stored in Simplified Chinese.  Keep stable Traditional labels
# for saved UAV Watcher selections while using the live route spelling.
_MISSAV_TAXONOMY_SLUGS = {
    '獨家': '独家',
    '單體作品': '单体作品',
    '多人運動': '多人运动',
    '騎乘': '骑乘',
    '4小時以上': '4小时以上',
    '苗條': '苗条',
    '戀物癖': '恋物癖',
    '企劃': '企划',
    '亂倫': '乱伦',
    '搭訕': '搭讪',
    '顏射': '颜射',
    '淫亂': '淫乱',
    '劇情': '剧情',
}


def _missav_taxonomy_target(kind, name, dm_id):
    encoded = quote(_MISSAV_TAXONOMY_SLUGS.get(name, name), safe='')
    prefix = f'/dm{dm_id}' if dm_id is not None else ''
    return _target(
        f'{kind}:{name.casefold()}',
        name,
        f'https://missav.ai{prefix}/{kind}/{encoded}',
    )


MISSAV_GENRES = [
    _missav_taxonomy_target('genres', name, dm_id)
    for name, dm_id in _MISSAV_GENRE_ROWS
]

_MISSAV_MAKER_ROWS = [
    ("Moody's", 825), ('Prestige', 68), ('Madonna', 269), ('S1', 188), ('SOD', 588),
    ('IdeaPocket', 572), ('Attackers', 510), ('Glory Quest', 2), ('ビッグモーカル', None),
    ('NATURAL HIGH', 715), ('Fc2', 406101), ('Takara Visual', 23), ('Wanz Factory', 152),
    ('Premium', 737), ('VENUS', 1), ('Fitch', 252), ("DEEP'S", 4), ('本中', 620),
    ('Hunter', 260), ('TMA', None), ('溜池ゴロー', 362), ('センタービレッジ', 1),
    ('Das', 377), ('Waap Entertainment', None), ('Crystal-Eizou', None), ('kawaii', 451),
    ('プラネットプラス', None), ('ゴーゴーズ', 2144), ('OPPAI', 697),
    ('STAR PARADISE', None), ('E-BODY', 184), ('セレブの友', None), ('ドグマ', 2),
    ('Alice Japan', 2), ('桃太郎映像出版', None), ('KM Produce', 1),
]
MISSAV_MAKERS = [
    _missav_taxonomy_target('makers', name, dm_id)
    for name, dm_id in _MISSAV_MAKER_ROWS
]

MISSAV_GROUPS = [
    _group('feeds', 'Feeds', MISSAV_FEEDS),
    _group('providers', 'Providers', MISSAV_PROVIDERS),
    _group('genres', 'Genres', MISSAV_GENRES),
    _group('makers', 'Makers', MISSAV_MAKERS),
]


SUPJAV_GROUPS = [
    _group('feeds', 'Feeds', [
        _target('feed:latest', '最近更新', 'https://supjav.com/'),
        _target('feed:popular', '熱門總榜', 'https://supjav.com/popular'),
        _target('feed:weekly-hot', '本週熱門', 'https://supjav.com/popular?sort=week'),
        _target('feed:monthly-hot', '本月熱門', 'https://supjav.com/popular?sort=month'),
    ]),
    _group('categories', 'Categories', [
        _target('category:uncensored', '無碼', 'https://supjav.com/category/uncensored-jav'),
        _target('category:censored', '有碼', 'https://supjav.com/category/censored-jav'),
        _target('category:amateur', '素人', 'https://supjav.com/category/amateur'),
        _target('category:chinese-subtitles', '中文字幕',
                'https://supjav.com/category/chinese-subtitles'),
        _target('category:english-subtitles', '英文字幕',
                'https://supjav.com/category/english-subtitles'),
        _target('category:reducing-mosaic', '破壞版',
                'https://supjav.com/category/reducing-mosaic'),
    ]),
]


_HANIME1_URLS = dict(Hanime1Browser.CATEGORIES)
HANIME1_GROUPS = [
    _group('feeds', 'Feeds', [
        _target(target_id, name, _HANIME1_URLS[name])
        for target_id, name in (
            ('sort:latest-release', '最新上市'),
            ('sort:latest-upload', '最新上傳'),
            ('sort:daily', '本日排行'),
            ('sort:weekly', '本週排行'),
            ('sort:monthly', '本月排行'),
            ('sort:views', '觀看次數'),
            ('sort:rating', '讚好比例'),
            ('sort:longest', '時長最長'),
            ('sort:watching', '他們在看'),
        )
    ]),
    _group('genres', 'Genres', [
        _target(f'genre:{index}', name, _HANIME1_URLS[name])
        for index, name in enumerate(Hanime1Browser.GENRES, start=1)
    ]),
    _group('dates', 'Dates', [
        _target(
            f'date:{name}', name,
            Hanime1Browser.filter_url(date=name),
        )
        for name in HANIME1_FILTER_DATES
    ]),
    _group('durations', 'Durations', [
        _target(
            f'duration:{name}', name,
            Hanime1Browser.filter_url(duration=name),
        )
        for name in HANIME1_FILTER_DURATIONS
    ]),
]

for _group_id, _tags in HANIME1_FILTER_TAG_GROUPS:
    HANIME1_GROUPS.append(_group(
        f'hanime-tags:{_group_id}',
        f'Hanime tags: {_group_id}',
        [
            _target(
                f'tag:{name}', name,
                Hanime1Browser.filter_url(tags=(name,)),
            )
            for name in _tags
        ],
        hanime_tag_group=True,
    ))


SITES = {
    'JableTV': {'browser': JableTVBrowser, 'groups': JABLE_GROUPS},
    'MissAV': {'browser': MissAVBrowser, 'groups': MISSAV_GROUPS},
    'SupJav': {'browser': SupJavBrowser, 'groups': SUPJAV_GROUPS},
    'Hanime1': {'browser': Hanime1Browser, 'groups': HANIME1_GROUPS},
}


_GROUP_LABEL_KEYS = {
    'feeds': 'st_group_feeds',
    'categories': 'st_group_categories',
    'providers': 'st_group_providers',
    'genres': 'st_group_genres',
    'makers': 'st_group_makers',
    'dates': 'st_group_dates',
    'durations': 'st_group_durations',
}

_HANIME_TAG_GROUP_LABEL_KEYS = {
    'features': 'st_hanime_tags_features',
    'relationships': 'st_hanime_tags_relationships',
    'roles': 'st_hanime_tags_roles',
    'characters': 'st_hanime_tags_characters',
    'appearance': 'st_hanime_tags_appearance',
    'clothing': 'st_hanime_tags_clothing',
    'locations': 'st_hanime_tags_locations',
    'themes': 'st_hanime_tags_themes',
    'acts': 'st_hanime_tags_acts',
}


def iter_targets(site_name):
    for group in SITES[site_name]['groups']:
        yield from group['targets']


def find_target(site_name, target_id=None, legacy_name=None):
    for target in iter_targets(site_name):
        if target_id and target['id'] == target_id:
            return target
        if legacy_name and legacy_name in (target['name'], target_label(target)):
            return target
    return None


def target_label(target):
    slug = target.get('tag_slug')
    if slug:
        return site_i18n.loc(site_i18n.TAGS, slug, target['name'])
    return site_i18n.loc(site_i18n.CATEGORY_I18N, target['url'], target['name'])


def group_label(group):
    if group.get('tag_group'):
        return site_i18n.loc(site_i18n.TAG_GROUPS, group['name'], group['name'])
    if group.get('hanime_tag_group'):
        group_id = group['id'].split(':', 1)[-1]
        return T(_HANIME_TAG_GROUP_LABEL_KEYS.get(group_id, group['name']))
    return T(_GROUP_LABEL_KEYS.get(group['id'], group['name']))


def selection_key(site_name, target_id):
    return f'{site_name}|{target_id}'
