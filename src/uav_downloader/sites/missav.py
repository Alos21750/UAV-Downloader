#!/usr/bin/env python
# coding: utf-8

import re
import threading
import cloudscraper
try:
    from curl_cffi import requests as cffi_requests
    _use_cffi = True
except ImportError:
    _use_cffi = False
from uav_downloader.sites.base import *
from bs4 import BeautifulSoup
from uav_downloader.i18n import sites as site_i18n


def _unpack_js_eval(script_text):
    """Decode Dean Edwards p,a,c,k,e,d packer."""
    match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',\s*(\d+),\s*(\d+),\s*'([^']*)'\s*\.split\('\|'\)",
        script_text, re.DOTALL
    )
    if not match:
        return None
    packed, a, c, keys_str = match.group(1), int(match.group(2)), int(match.group(3)), match.group(4).split('|')
    # Guard against malformed packer params: base<=1 makes to_base loop forever;
    # an absurd `c` would allocate an unbounded lookup dict. Bail on either.
    if a <= 1 or c < 0 or c > 200000:
        return None

    def to_base(n, base):
        digits = '0123456789abcdefghijklmnopqrstuvwxyz'
        if n == 0: return '0'
        s = ''
        while n:
            s = digits[n % base] + s
            n //= base
        return s

    lookup = {to_base(i, a): (keys_str[i] if i < len(keys_str) and keys_str[i] else to_base(i, a))
              for i in range(c)}
    return re.sub(r'\b(\w+)\b', lambda m: lookup.get(m.group(0), m.group(0)), packed)


class SiteMissAV(M3U8Crawler):
    """Downloader for missav.ai"""
    # MissAV's surrit.com CDN becomes unreliable under the previous worst case
    # of 5 simultaneous videos x 16 segment workers.  Keep each video modest,
    # bound all MissAV instances together, and allow a longer resumable tail
    # retry for isolated 429/timeout failures near 100%.
    segment_worker_cap = 4
    segment_retry_rounds = 10
    segment_retry_base_delay = 1.5
    segment_retry_max_delay = 8.0
    _segment_request_gate = threading.BoundedSemaphore(8)
    # Matches video pages ONLY (no dm\d+ routing prefix — those are category pages):
    #   https://missav.ai/cn/sone-543-chinese-subtitle
    #   https://missav.ai/sone-543
    #   https://missav.ai/dm1151/092014_887
    #   https://missav.ai/dm464/081012-097
    # Does NOT match:
    #   https://missav.ai/dm278/chinese-subtitle  (category listing)
    website_pattern = r'https://(?:www\.)?(?:missav\.(?:ai|ws|live)|missav123\.com)/(?:dm\d+/)?(?:cn|en|ja|ko|ms|th)/([a-zA-Z0-9][a-zA-Z0-9\-_]+)|https://(?:www\.)?(?:missav\.(?:ai|ws|live)|missav123\.com)/([a-zA-Z0-9][a-zA-Z0-9\-_]*[-_]\d[a-zA-Z0-9\-_]*)'
    website_dirname_pattern = r'https://(?:www\.)?(?:missav\.(?:ai|ws|live)|missav123\.com)/(?:dm\d+/)?(?:(?:cn|en|ja|ko|ms|th)/)?([a-zA-Z0-9][a-zA-Z0-9\-_]*[-_]\d[a-zA-Z0-9\-_]*)'

    _shared_scraper = None
    _scraper_lock = threading.Lock()

    def _scrape(self, task):
        with self._segment_request_gate:
            return super()._scrape(task)

    @classmethod
    def _get_scraper(cls):
        with cls._scraper_lock:
            if cls._shared_scraper is None:
                if _use_cffi:
                    cls._shared_scraper = cffi_requests.Session(impersonate='chrome')
                else:
                    cls._shared_scraper = cloudscraper.create_scraper(
                        browser=request_headers, delay=10)
            return cls._shared_scraper

    def get_url_infos(self):
        scraper = self._get_scraper()
        hf = lambda host: {'Referer': f'https://{host}/', 'Origin': f'https://{host}'}
        def _validate(resp):
            return ('og:title' in resp.text) and (('m3u8' in resp.text) or ('eval(function(p,a,c,k,e,d)' in resp.text))
        resp, host, reason = fetch_with_mirrors(scraper, self._url, 'missav', _validate, headers_factory=hf)
        if reason != 'ok':
            if reason == 'blocked':
                raise MirrorsBlockedError("所有鏡像都被 Cloudflare 阻擋（可能是你的網路/IP 信譽問題，請改用 VPN 或不同網路）")
            raise Exception(f"頁面解析失敗（版面改版或影片不存在）: {self._url}")
        self._extra_headers = {'Referer': f'https://{host}/', 'Origin': f'https://{host}'}
        htmlfile = resp

        # Title from og:title
        og_title = re.search(r'og:title"\s+content="([^"]+)"', htmlfile.text)
        if og_title:
            self._targetName = og_title.group(1)
        else:
            soup = BeautifulSoup(htmlfile.content, 'html.parser')
            meta = soup.find('meta', property='og:title')
            self._targetName = meta.get('content', '') if meta else ''

        # Thumbnail from og:image
        og_image = re.search(r'og:image"\s+content="([^"]+)"', htmlfile.text)
        if og_image:
            self._imageUrl = og_image.group(1)

        # Extract m3u8 from packed eval blocks
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', htmlfile.text, re.DOTALL)
        for script in scripts:
            if 'eval(function' not in script or 'm3u8' not in script:
                continue
            unpacked = _unpack_js_eval(script)
            if unpacked:
                # Unpacked text has escaped quotes: source=\'https://...\'
                # Match "source=" (not source842= etc.) followed by the URL
                main_match = re.search(
                    r"source\s*=\s*[\\']*(https?://[^'\\;\s]+\.m3u8)", unpacked)
                if main_match:
                    self._m3u8url = main_match.group(1)
                    return
                # Fallback: any m3u8 URL in the block
                any_match = re.search(r'(https?://[^\'\\;\s]+\.m3u8)', unpacked)
                if any_match:
                    self._m3u8url = any_match.group(1)
                    return

        raise Exception(f"Could not find m3u8 URL for {self._url}")


class MissAVBrowser:
    """Fetches categories and video listings from missav.ai for the browse GUI."""
    _scraper = None
    _scraper_lock = __import__('threading').Lock()

    # Fixed category list: no language segment means MissAV default Traditional Chinese.
    # Non-empty language codes (e.g. 'cn', 'en', 'ja') are inserted as prefixes.
    CATEGORIES = [
        ('今日熱門', 'https://missav.ai/dm298/today-hot'),
        ('本週熱門', 'https://missav.ai/dm170/weekly-hot'),
        ('本月熱門', 'https://missav.ai/dm270/monthly-hot'),
        ('中文字幕', 'https://missav.ai/dm278/chinese-subtitle'),
        ('最近更新', 'https://missav.ai/dm539/new'),
        ('新作上市', 'https://missav.ai/dm634/release'),
        ('無碼流出', 'https://missav.ai/dm817/uncensored-leak'),
        ('SIRO', 'https://missav.ai/dm36/siro'),
        ('FC2', 'https://missav.ai/dm541/fc2'),
        ('麻豆傳媒', 'https://missav.ai/dm63/madou'),
        ('東京熱', 'https://missav.ai/dm42/tokyohot'),
        ('一本道', 'https://missav.ai/dm4854130/1pondo'),
    ]

    @classmethod
    def _get_scraper(cls):
        with cls._scraper_lock:
            if cls._scraper is None:
                if _use_cffi:
                    cls._scraper = cffi_requests.Session(impersonate='chrome')
                else:
                    cls._scraper = cloudscraper.create_scraper(browser=request_headers, delay=10)
            return cls._scraper

    @classmethod
    def fetch_categories(cls, lang=''):
        """Return categories with URLs localized to *lang*.

        MissAV defaults to Traditional Chinese, so an empty string means no prefix.
        Other languages get /{lang}/ inserted after the /dm{N}/ segment.
        """
        cats = []
        for name, url in cls.CATEGORIES:
            localized_name = site_i18n.loc(site_i18n.CATEGORY_I18N, url, name)
            if lang:
                # Insert language prefix: .../dm296/en/today-hot
                url = re.sub(r'(/dm\d+/)', rf'\1{lang}/', url)
            cats.append({'name': localized_name, 'url': url, 'count': 0})
        return cats

    @classmethod
    def fetch_page(cls, url):
        def _validate(resp):
            if getattr(resp, 'status_code', 0) != 200:
                return False
            s = BeautifulSoup(resp.content, 'html.parser')
            return bool(s.select('div.thumbnail') or s.select('div.group, article.video-item, div[class*="grid"] > div'))
        resp, host, reason = fetch_with_mirrors(cls._get_scraper(), url, 'missav', _validate)
        if reason != 'ok':
            if reason == 'blocked':
                raise MirrorsBlockedError(url)
            return []
        try:
            soup = BeautifulSoup(resp.content, 'html.parser')
            cards = soup.select('div.thumbnail') or soup.select('div.group, article.video-item, div[class*="grid"] > div')
            videos = []
            from urllib.parse import urljoin
            for card in cards:
                link = card.select_one('a[href]')
                if not link:
                    continue
                video_url = urljoin(str(resp.url), link.get('href', ''))
                if '/search/' in video_url:
                    continue
                last_seg = video_url.rstrip('/').rsplit('/', 1)[-1].split('?')[0]
                if not re.search(r'\d', last_seg):
                    continue
                img = card.select_one('img')
                thumbnail = (img.get('data-src', '') or img.get('src', '')) if img else ''
                title_text = img.get('alt', '') if img else ''
                title_a = card.select_one('div.my-2 a, div.truncate a')
                if title_a:
                    title_text = title_a.get_text(strip=True) or title_text
                duration_span = card.select_one('span.absolute.bottom-1.right-1')
                duration = duration_span.get_text(strip=True) if duration_span else ''
                videos.append({'url': video_url, 'title': title_text, 'thumbnail': thumbnail, 'duration': duration})
            return videos
        except Exception:
            return []

    @classmethod
    def search(cls, query, lang=''):
        """Search for videos matching query."""
        from urllib.parse import quote
        q = quote(query, safe='')
        if lang:
            url = f'https://missav.ai/{lang}/search/{q}'
        else:
            url = f'https://missav.ai/search/{q}'
        return cls.fetch_page(url)

    @classmethod
    def page_url(cls, base_url, page):
        """Build paginated URL."""
        if page <= 1:
            return base_url
        sep = '&' if '?' in base_url else '?'
        return f'{base_url}{sep}page={page}'
