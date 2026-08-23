#!/usr/bin/env python
# coding: utf-8
"""Hanime1.me direct-MP4 downloader and browse/search adapter."""

import html
import re
import threading
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import cloudscraper
try:
    from curl_cffi import requests as cffi_requests
    _use_cffi = True
except ImportError:
    _use_cffi = False
from bs4 import BeautifulSoup

from uav_downloader.core import config
from uav_downloader.i18n import sites as site_i18n
from uav_downloader.sites.base import (
    MirrorsBlockedError,
    get_resolution_pref,
    request_headers,
)
from uav_downloader.sites.supjav import SiteSupJav


HANIME1_ROOT = 'https://hanime1.me'
HANIME1_HOME = HANIME1_ROOT + '/'
_BLOCKED_STATUS = frozenset({403, 429, 503})
_browser_local = threading.local()

HANIME1_FILTER_SORTS = (
    '最新上市', '最新上傳', '本日排行', '本週排行', '本月排行',
    '觀看次數', '讚好比例', '時長最長', '他們在看',
)
HANIME1_FILTER_GENRES = (
    '裏番', '泡麵番', 'Motion Anime', '3DCG', '2.5D',
    '2D動畫', 'AI生成', 'MMD', 'Cosplay',
)
HANIME1_FILTER_DATES = (
    '過去 24 小時', '過去 2 天', '過去 1 週',
    '過去 1 個月', '過去 3 個月', '過去 1 年',
)
HANIME1_FILTER_DURATIONS = (
    '1 分鐘 +', '5 分鐘 +', '10 分鐘 +', '20 分鐘 +',
    '30 分鐘 +', '60 分鐘 +', '0 - 10 分鐘', '0 - 20 分鐘',
)

# Snapshot of Hanime1's public filter vocabulary, verified against /search on
# 2026-08-23.  The filter dialog refreshes this from the live page when it can;
# this complete fallback keeps the UI useful during a temporary block/offline
# start and gives UAV Watcher stable IDs for saved selections.
HANIME1_FILTER_TAG_GROUPS = (
    ('features', (
        '無碼', 'AI解碼', '中文字幕', '中文配音', '同人作品', '斷面圖',
        'ASMR', '1080p', '60FPS',
    )),
    ('relationships', (
        '近親', '姐', '妹', '母', '女兒', '師生', '情侶', '青梅竹馬',
        '同事', 'JK', '處女', '御姐', '熟女', '人妻',
    )),
    ('roles', (
        '女教師', '男教師', '女醫生', '女病人', '護士', 'OL', '女警',
        '大小姐', '偶像', '女僕', '巫女', '魔女', '修女', '風俗娘',
        '公主', '女忍者', '女戰士', '女騎士', '魔法少女',
    )),
    ('characters', (
        '異種族', '天使', '妖精', '魔物娘', '魅魔', '吸血鬼', '女鬼',
        '獸娘', '福瑞', '乳牛', '機械娘', '碧池', '痴女', '雌小鬼',
        '不良少女', '傲嬌', '病嬌', '無口', '無表情', '眼神死',
        '正太', '偽娘', '扶他',
    )),
    ('appearance', (
        '短髮', '馬尾', '雙馬尾', '丸子頭', '巨乳', '乳環', '舌環',
        '貧乳', '黑皮膚', '曬痕', '眼鏡娘', '獸耳', '尖耳朵',
        '異色瞳', '美人痣', '肌肉女', '白虎', '陰毛', '腋毛',
        '大屌', '黑屌',
    )),
    ('clothing', (
        '著衣', '水手服', '體操服', '泳裝', '比基尼', '死庫水', '和服',
        '兔女郎', '圍裙', '啦啦隊', '絲襪', '吊襪帶', '熱褲',
        '迷你裙', '性感內衣', '緊身衣', '丁字褲', '高跟鞋', '睡衣',
        '婚紗', '旗袍', '古裝', '哥德', '口罩', '刺青', '淫紋',
        '身體寫字',
    )),
    ('locations', (
        '校園', '教室', '圖書館', '保健室', '體育倉庫', '游泳池',
        '愛情賓館', '醫院', '辦公室', '浴室', '窗邊', '公共廁所',
        '公眾場合', '戶外野戰', '電車', '車震', '遊艇', '露營帳篷',
        '電影院', '健身房', '沙灘', '溫泉', '夜店', '監獄', '教堂',
    )),
    ('themes', (
        '純愛', '戀愛喜劇', '後宮', '十指緊扣', '開大車', 'NTR',
        '精神控制', '藥物', '痴漢', '阿嘿顏', '哭泣', '精神崩潰',
        '獵奇', 'BDSM', '綑綁', '眼罩', '項圈', '調教', '異物插入',
        '尋歡洞', '肉便器', '性奴隸', '胃凸', '強制', '輪姦', '凌辱',
        '性暴力', '逆強制', '女王樣', '榨精', '母女丼', '姐妹丼',
        '出軌', '醉酒', '攝影', '睡眠姦', '機械姦', '蟲姦', '性轉換',
        '百合', '耽美', '時間停止', '異世界', '怪獸', '哥布林',
        '世界末日',
    )),
    ('acts', (
        '手交', '指交', '玩乳頭', '乳交', '乳頭交', '肛交', '雙洞齊下',
        '腳交', '素股', '拳交', '3P', '群交', '口交', '跪舔',
        '深喉嚨', '口爆', '吞精', '舔蛋蛋', '舔穴', '69', '自慰',
        '腋交', '舔腋下', '髮交', '舔耳朵', '舔腳', '內射', '外射',
        '顏射', '潮吹', '懷孕', '噴奶', '放尿', '排便', '騎乘位',
        '背後位', '側面位', '顏面騎乘', '火車便當', '一字馬', '性玩具',
        '飛機杯', '跳蛋', '毒龍鑽', '觸手', '獸交', '頸手枷',
        '扯頭髮', '掐脖子', '打屁股', '肉棒打臉', '陰道外翻',
        '男乳首責', '接吻', '舌吻', 'POV',
    )),
)
HANIME1_FILTER_TAGS = tuple(
    tag for _group_id, tags in HANIME1_FILTER_TAG_GROUPS for tag in tags)


def _make_scraper():
    """Return a browser-fingerprint session able to establish Hanime1 cookies."""
    if _use_cffi:
        return cffi_requests.Session(impersonate='chrome')
    return cloudscraper.create_scraper(browser=request_headers, delay=10)


def _blocked_response(response) -> bool:
    status = int(getattr(response, 'status_code', 0) or 0)
    if status in _BLOCKED_STATUS:
        return True
    body = str(getattr(response, 'text', '') or '')[:20000].casefold()
    return ('attention required' in body or 'just a moment' in body or
            'cf-chl-' in body)


def _request(session, url, *, timeout=30):
    response = session.get(
        url,
        headers={'Referer': HANIME1_HOME},
        timeout=timeout,
        allow_redirects=True,
        **config.proxy_request_kwargs(),
    )
    if _blocked_response(response):
        raise MirrorsBlockedError(url)
    return response


def _prime_session(session):
    """Visit the homepage first; Hanime1 issues both XSRF and session cookies there."""
    response = _request(session, HANIME1_HOME)
    if int(getattr(response, 'status_code', 0) or 0) != 200:
        raise Exception(f'Hanime1 初始化失敗 (HTTP {response.status_code})')
    return session


def _mp4_height(url, label=''):
    value = f'{label} {url}'
    match = re.search(r'(?<!\d)(\d{3,4})\s*p(?!\w)', value, re.I)
    return int(match.group(1)) if match else None


def _extract_sources(soup):
    """Extract fresh signed progressive-MP4 sources from watch/download markup."""
    sources = []
    seen = set()

    def _add(raw_url, label=''):
        url = html.unescape(str(raw_url or '').strip()).replace('\\/', '/')
        try:
            parsed = urlsplit(url)
        except ValueError:
            return
        if (parsed.scheme.casefold() != 'https' or not parsed.hostname or
                not parsed.path.casefold().endswith('.mp4') or url in seen):
            return
        seen.add(url)
        sources.append({'url': url, 'height': _mp4_height(url, label)})

    for element in soup.select('source[src], a[data-url], a[href]'):
        raw_url = element.get('src') or element.get('data-url') or element.get('href')
        _add(raw_url, element.get_text(' ', strip=True))

    # Keep a conservative fallback for MP4 URLs embedded in inline JSON/JavaScript.
    markup = html.unescape(str(soup)).replace('\\/', '/')
    for raw_url in re.findall(r'https://[^\s\'"<>]+?\.mp4(?:\?[^\s\'"<>]*)?', markup, re.I):
        _add(raw_url)
    return sources


def _select_source(sources, preference):
    """Apply the same highest/lowest/target-or-below policy as HLS variants."""
    items = list(sources or [])
    if not items:
        return None
    known = [item for item in items if isinstance(item.get('height'), int)]
    pref = str(preference or '').strip().casefold()
    if pref == 'lowest':
        return min(known, key=lambda item: item['height']) if known else items[0]
    if pref in {'1080', '720', '480', '360'}:
        if not known:
            return items[0]
        target = int(pref)
        at_or_below = [item for item in known if item['height'] <= target]
        if at_or_below:
            return max(at_or_below, key=lambda item: item['height'])
        return min(known, key=lambda item: item['height'])
    return max(known, key=lambda item: item['height']) if known else items[0]


def _clean_text(value):
    return ' '.join(html.unescape(str(value or '')).replace('\xa0', ' ').split())


def _extract_title(soup):
    heading = soup.select_one('h3.video-details-wrapper')
    if heading:
        title = _clean_text(heading.get_text(' ', strip=True))
        if title:
            return title
    meta = soup.select_one('meta[property="og:title"][content]')
    title = _clean_text(meta.get('content')) if meta else ''
    title = re.sub(r'\s+-\s+Hanime1\.me\s*$', '', title, flags=re.I)
    if title:
        return title
    return _clean_text(soup.title.get_text(' ', strip=True) if soup.title else '')


def _extract_image(soup):
    meta = soup.select_one('meta[property="og:image"][content]')
    raw = meta.get('content') if meta else ''
    if not raw:
        video = soup.select_one('video[poster]')
        raw = video.get('poster') if video else ''
    image_url = urljoin(HANIME1_HOME, html.unescape(str(raw or '').strip()))
    try:
        return image_url if urlsplit(image_url).scheme.casefold() == 'https' else None
    except ValueError:
        return None


def _parse_videos(soup, listing_url=''):
    videos = []
    seen = set()
    for card in soup.select('.video-item-container'):
        anchor = card.select_one('a.video-link[href]')
        if not anchor:
            continue
        raw_url = urljoin(HANIME1_HOME, anchor.get('href', ''))
        video_id = SiteHanime1.validate_url(raw_url)
        if not video_id:
            continue
        video_url = f'{HANIME1_ROOT}/watch?v={video_id}'
        if video_url in seen:
            continue
        seen.add(video_url)

        title_el = card.select_one('.title')
        title = _clean_text(
            title_el.get_text(' ', strip=True) if title_el else card.get('title', ''))
        image = card.select_one('img.main-thumb, img[data-src], img[src]')
        thumbnail = ''
        if image:
            thumbnail = urljoin(
                HANIME1_HOME,
                image.get('data-src') or image.get('src') or '',
            )
        duration_el = card.select_one('.duration')
        time_el = card.select_one('.subtitle-time')
        date_text = _clean_text(time_el.get_text(' ', strip=True) if time_el else '')
        date_text = re.sub(r'^[•·\-]\s*', '', date_text)
        author_el = card.select_one('.subtitle a[href]')
        item = {
            'url': video_url,
            'title': title,
            'thumbnail': thumbnail,
            'duration': _clean_text(
                duration_el.get_text(' ', strip=True) if duration_el else ''),
            'date': date_text,
            'author': _clean_text(
                author_el.get_text(' ', strip=True) if author_el else ''),
        }
        if listing_url:
            item['_source_listing_url'] = listing_url
        videos.append(item)

    # 裏番 / 泡麵番 use a compact cover grid instead of the horizontal cards
    # used by sort/tag/search results.  These cards intentionally omit date,
    # duration and author; UAV Watcher confirms the absolute date from the
    # detail page before applying its baseline.
    for anchor in soup.select('a[href]'):
        card = anchor.select_one('.home-rows-videos-div.search-videos')
        if card is None:
            continue
        raw_url = urljoin(HANIME1_HOME, anchor.get('href', ''))
        video_id = SiteHanime1.validate_url(raw_url)
        if not video_id:
            continue
        video_url = f'{HANIME1_ROOT}/watch?v={video_id}'
        if video_url in seen:
            continue
        title_el = card.select_one('.home-rows-videos-title')
        title = _clean_text(
            title_el.get_text(' ', strip=True) if title_el else '')
        if not title:
            continue
        image = card.select_one('img[src], img[data-src]')
        thumbnail = ''
        if image:
            thumbnail = urljoin(
                HANIME1_HOME,
                image.get('data-src') or image.get('src') or '',
            )
        seen.add(video_url)
        item = {
            'url': video_url,
            'title': title,
            'thumbnail': thumbnail,
            'duration': '',
            'date': '',
            'author': '',
        }
        if listing_url:
            item['_source_listing_url'] = listing_url
        videos.append(item)
    return videos


def _extract_published_date(soup):
    """Return the confirmed YYYY-MM-DD shown on a Hanime1 watch page."""
    for element in soup.select('.video-details-wrapper, time[datetime]'):
        value = element.get('datetime') or element.get_text(' ', strip=True)
        match = re.search(r'(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)', value)
        if match:
            try:
                year, month, day = map(int, match.groups())
                # Validate impossible dates instead of trusting matching text.
                from datetime import date
                date(year, month, day)
            except ValueError:
                continue
            return match.group(0)
    return ''


def _parse_filter_catalog(soup):
    def _values(selector, *, exclude=()):
        result = []
        excluded = set(exclude)
        for element in soup.select(selector):
            value = _clean_text(element.get('data-value') or element.get('value'))
            if not value or value in excluded or value in result:
                continue
            result.append(value)
        return tuple(result)

    return {
        'genres': _values('#genre-modal .genre-option[data-value]', exclude=('全部',)),
        'sorts': _values('#sort-modal .hentai-sort-options-wrapper[data-value]'),
        'dates': _values('#date-modal .hentai-date-options-wrapper[data-value]'),
        'durations': _values(
            '#duration-modal .hentai-duration-options-wrapper[data-value]'),
        'tags': _values('input[type="checkbox"][name="tags[]"][value]'),
    }


def _filter_url(**params):
    pairs = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((key, item) for item in value)
        elif value not in (None, ''):
            pairs.append((key, value))
    query = urlencode(pairs, doseq=True)
    return f'{HANIME1_ROOT}/search' + (f'?{query}' if query else '')


class SiteHanime1(SiteSupJav):
    website_pattern = r'https://(?:www\.)?hanime1\.me/watch\?v=\d+$'
    website_dirname_pattern = r'https://(?:www\.)?hanime1\.me/watch\?v=(\d+)$'
    direct_site_name = 'Hanime1'
    direct_default_referer = HANIME1_HOME

    def get_url_infos(self):
        self._direct_url = None
        self._direct_referer = None
        self._m3u8url = None
        with _make_scraper() as scraper:
            _prime_session(scraper)
            response = _request(scraper, self._url)
            status = int(getattr(response, 'status_code', 0) or 0)
            if status != 200:
                raise Exception(f'Hanime1 影片頁讀取失敗 (HTTP {status})')
            soup = BeautifulSoup(response.content, 'html.parser')
            sources = _extract_sources(soup)

            # Older/alternate pages may put the signed URLs only on /download.
            if not sources:
                video_id = self.validate_url(self._url)
                download_url = f'{HANIME1_ROOT}/download?v={video_id}'
                download_response = _request(scraper, download_url)
                download_status = int(
                    getattr(download_response, 'status_code', 0) or 0)
                if download_status == 200:
                    sources = _extract_sources(BeautifulSoup(
                        download_response.content, 'html.parser'))

        selected = _select_source(sources, get_resolution_pref())
        if not selected:
            raise Exception('此 Hanime1 影片目前沒有可用的 MP4 下載來源')
        title = _extract_title(soup)
        if not title:
            raise Exception('Hanime1 影片標題解析失敗（版面可能已改版）')
        self._targetName = title
        self._imageUrl = _extract_image(soup)
        self._direct_url = selected['url']
        self._direct_referer = self._url
        self._extra_headers = {'Referer': self._url}


class Hanime1Browser:
    _url_root = HANIME1_ROOT

    SORTS = HANIME1_FILTER_SORTS
    GENRES = HANIME1_FILTER_GENRES
    FEATURE_TAGS = ('中文字幕', '中文配音', '無碼', 'AI解碼', '1080p', '60FPS')
    CATEGORIES = (
        [(name, _filter_url(sort=name)) for name in SORTS] +
        [(name, _filter_url(genre=name)) for name in GENRES] +
        [(name, _filter_url(**{'tags[]': [name]})) for name in FEATURE_TAGS]
    )
    HOMEPAGE_SECTIONS = tuple(CATEGORIES)

    @classmethod
    def _get_scraper(cls):
        scraper = getattr(_browser_local, 'scraper', None)
        if scraper is None:
            scraper = _make_scraper()
            try:
                _prime_session(scraper)
            except Exception:
                try:
                    scraper.close()
                except Exception:
                    pass
                raise
            _browser_local.scraper = scraper
        return scraper

    @classmethod
    def _reset_scraper(cls):
        scraper = getattr(_browser_local, 'scraper', None)
        if scraper is not None:
            try:
                scraper.close()
            except Exception:
                pass
        _browser_local.scraper = None

    @classmethod
    def fetch_categories(cls):
        return [{
            'name': site_i18n.loc(site_i18n.CATEGORY_I18N, url, name),
            'url': url,
            'count': 0,
        } for name, url in cls.CATEGORIES]

    @classmethod
    def filter_catalog(cls):
        return {
            'genres': HANIME1_FILTER_GENRES,
            'sorts': HANIME1_FILTER_SORTS,
            'dates': HANIME1_FILTER_DATES,
            'durations': HANIME1_FILTER_DURATIONS,
            'tags': HANIME1_FILTER_TAGS,
        }

    @classmethod
    def fetch_filter_catalog(cls):
        """Refresh the filter vocabulary, retaining complete offline fallbacks."""
        fallback = cls.filter_catalog()
        try:
            response = _request(cls._get_scraper(), _filter_url())
            if int(getattr(response, 'status_code', 0) or 0) != 200:
                return fallback
            live = _parse_filter_catalog(
                BeautifulSoup(response.content, 'html.parser'))
        except Exception:
            return fallback
        return {
            key: tuple(live.get(key) or fallback[key])
            for key in fallback
        }

    @classmethod
    def filter_url(
            cls, *, query='', genre='', sort='', date='', duration='', tags=()):
        unique_tags = []
        for tag in tags or ():
            value = str(tag or '').strip()
            if value and value not in unique_tags:
                unique_tags.append(value)
        return _filter_url(
            query=str(query or '').strip(),
            genre=str(genre or '').strip(),
            sort=str(sort or '').strip(),
            date=str(date or '').strip(),
            duration=str(duration or '').strip(),
            **{'tags[]': unique_tags},
        )

    @classmethod
    def fetch_video_date(cls, url):
        if not SiteHanime1.validate_url(str(url or '')):
            return ''
        response = _request(cls._get_scraper(), str(url))
        if int(getattr(response, 'status_code', 0) or 0) != 200:
            return ''
        return _extract_published_date(
            BeautifulSoup(response.content, 'html.parser'))

    @classmethod
    def _listing_url_allowed(cls, url):
        try:
            parsed = urlsplit(str(url or ''))
        except ValueError:
            return False
        return (
            parsed.scheme.casefold() == 'https' and
            not parsed.username and not parsed.password and
            (parsed.hostname or '').casefold() in {'hanime1.me', 'www.hanime1.me'} and
            parsed.path.rstrip('/') in {'', '/search'}
        )

    @classmethod
    def fetch_page(cls, url):
        if not cls._listing_url_allowed(url):
            return []
        for attempt in range(2):
            try:
                response = _request(cls._get_scraper(), url)
                break
            except MirrorsBlockedError:
                cls._reset_scraper()
                if attempt:
                    raise
        else:
            return []
        if int(getattr(response, 'status_code', 0) or 0) != 200:
            return []
        try:
            return _parse_videos(
                BeautifulSoup(response.content, 'html.parser'), str(url))
        except Exception:
            return []

    @classmethod
    def page_url(cls, base, page):
        if page <= 1:
            return base
        parsed = urlsplit(str(base or ''))
        pairs = [(key, value) for key, value in parse_qsl(
            parsed.query, keep_blank_values=True) if key != 'page']
        pairs.append(('page', str(int(page))))
        return urlunsplit((
            parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), ''))

    @classmethod
    def search_url(cls, query):
        return cls.filter_url(query=query)

    @classmethod
    def search(cls, query):
        return cls.fetch_page(cls.search_url(query))
