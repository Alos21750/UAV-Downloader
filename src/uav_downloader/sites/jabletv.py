#!/usr/bin/env python
# coding: utf-8

import re
import cloudscraper
try:
    from curl_cffi import requests as cffi_requests
    _use_cffi = True
except ImportError:
    _use_cffi = False
import threading as _threading
from uav_downloader.sites.base import *
from bs4 import BeautifulSoup
from uav_downloader.i18n import sites as site_i18n


_browser_scraper = None
_browser_scraper_lock = _threading.Lock()

def _make_scraper():
    """Fresh scraper: curl_cffi (Cloudflare-capable) if available, else cloudscraper."""
    if _use_cffi:
        return cffi_requests.Session(impersonate='chrome')
    return cloudscraper.create_scraper(browser=request_headers, delay=10)


def _apply_jable_lang(scraper):
    """Set JableTV's kt_rt_lang cookie from the current UI language so titles/listings/
    categories come back in en/jp (empty -> default Traditional Chinese). Applies to both
    jable.tv and the fs1.app mirror. Safe/idempotent; tolerates curl_cffi or cloudscraper."""
    try:
        from uav_downloader.i18n.locales import T
        val = T('jable_lang') or ''
    except Exception:
        val = ''
    for dom in ('.jable.tv', '.fs1.app'):
        try:
            scraper.cookies.set('kt_rt_lang', val, domain=dom)
        except Exception:
            pass


class SiteJableTV(M3U8Crawler):
    # The captured identifier is used as a segment directory. Accept exactly
    # one canonical slug, never path separators, dot segments or URL syntax.
    website_pattern = r'https://jable\.tv/videos/[a-z0-9_-]+/\Z'
    website_dirname_pattern = r'https://jable\.tv/videos/([a-z0-9_-]+)/\Z'

    def get_url_infos(self):
        with _make_scraper() as scraper:
            def _validate(resp):
                return ('og:title' in resp.text) and ('m3u8' in resp.text)
            _apply_jable_lang(scraper)
            htmlfile, host, reason = fetch_with_mirrors(scraper, self._url, 'jable', _validate, timeout=30)
        if reason != 'ok':
            if reason == 'blocked':
                raise MirrorsBlockedError("所有鏡像都被 Cloudflare 阻擋（可能是你的網路/IP 信譽問題，請改用 VPN 或不同網路）")
            raise Exception(f"頁面解析失敗（版面改版或影片不存在）: {self._url}")
        parsed = self._parse_page(htmlfile.text)
        if not parsed:
            raise Exception(f"頁面解析失敗（可能被 Cloudflare 阻擋或版面改版）: {self._url}")
        self._targetName, self._imageUrl, self._m3u8url = parsed

    @staticmethod
    def _parse_page(text):
        """Extract (title, image_url, m3u8_url) from a video page, or None if any is
        missing. Precise captures instead of greedy `.+` — on minified/single-line HTML a
        greedy `.+` spans to the LAST delimiter on the line, yielding a wrong title/URL."""
        title = re.search(r'og:title"\s+content="([^"]+)"', text)
        image = re.search(r'og:image"\s+content="([^"]+)"', text)
        m3u8 = re.search(r'https://[^\s"\']+\.m3u8', text)  # bounded to one URL token
        if not (title and image and m3u8):
            return None
        return title.group(1), image.group(1), m3u8.group(0)


class SiteJableTV_Backup(SiteJableTV):
    website_pattern = r'https://fs1\.app/videos/[a-z0-9_-]+/\Z'
    website_dirname_pattern = r'https://fs1\.app/videos/([a-z0-9_-]+)/\Z'


class JableTVList(SiteUrlList_M3U8):
    _sortby_dict = {'最高相關': '',
                   '近期最佳': 'post_date_and_popularity',
                   '最近更新': 'post_date',
                   '最多觀看': 'video_viewed',
                   '最高收藏': 'most_favourited'}

    _url_root = 'https://jable.tv'

    def __init__(self, url, silence=False):
        self.islist = None
        if not url.startswith(self._url_root): return
        self.islist = self._url_get(url)
        if self.islist is None:
            if not silence:
                print(f"網址 {url} 錯誤!!", flush=True)
            return

        titleBox = self._soup.find('div', class_='title-box')
        if titleBox and titleBox.span:
            count_text = str(titleBox.span.get_text(strip=True)).partition(" ")[0]
            self.totalLinks = int(count_text) if count_text.isdigit() else 0
        else:
            self.totalLinks = len(self.links)
        self.listType = titleBox.h2.string if titleBox and titleBox.h2 else ""

        sort_list = self._soup.find('ul', id=lambda x: x and '_sort_list' in str(x))
        if sort_list:
            activeSortType = sort_list.find('li', class_='active')
        else:
            activeSortType = self._soup.find('li', class_='active')
        if activeSortType is None: self.sortType = None
        else:  self.sortType = str(activeSortType.a.string)
        self.totalPages = (self.totalLinks + 23) // 24
        self.currentPage = 0
        self.searchKeyWord = None
        uu = url.split("/")
        if self._url_root == '/'.join(uu[0:3]):
            if len(uu)>3:
                self.url = '/'.join(uu[:-1])+'/'
                if 'search' == uu[3]:
                    self.searchKeyWord = uu[4]
        if not silence:
            print(f"[{self.listType} {str(self.sortType)}]共有{self.totalPages}頁，{self.totalLinks}部影片。已取得{len(self.links)}部影片")

    def _url_get(self, url):
        divlist = None
        try:
            with _make_scraper() as _scr:
                def _validate(resp):
                    s = BeautifulSoup(resp.content, 'html.parser')
                    return bool(s.find('div', id=lambda x: x and x.startswith('list_videos')) or s.find('div', id='site-content'))
                _apply_jable_lang(_scr)
                htmlfile, host, reason = fetch_with_mirrors(_scr, url, 'jable', _validate, timeout=30)
            if reason == 'ok':
                content = htmlfile.content
                soup = BeautifulSoup(content, 'html.parser')
                self._soup = soup
                divlist = soup.find('div', id=lambda x: x and x.startswith('list_videos'))
                if divlist is None:
                    divlist = soup.find('div', id="site-content")
                    if divlist: divlist = divlist.div
                divlists_MemberOnly = soup.find_all('div', class_="ribbon-top-left")
                _memberOnly_urls = [del_url.find_parent('a')['href'] for del_url in divlists_MemberOnly if del_url.getText() == '會員']
                if divlist is None: return None
                self.links = []
                self.linkDescriptions = []
                self.thumbnails = []
                tags = divlist.select('div.detail')
                for tag in tags:
                    if not tag.h6 or not tag.h6.a: continue
                    tag_a = tag.h6.a
                    _url = tag_a['href']
                    if _url not in _memberOnly_urls:
                        self.links.append(_url)
                        self.linkDescriptions.append(str(tag_a.string or ''))
                        card = tag.find_parent('div', class_='video-img-box')
                        thumb = ''
                        if card:
                            img = card.select_one('img')
                            if img:
                                thumb = img.get('data-src', '') or img.get('src', '')
                        self.thumbnails.append(thumb)
            return divlist

        except Exception:
            return divlist

    def getSortTypeList(self):
        ll = list(JableTVList._sortby_dict)
        if self.searchKeyWord is None: del ll[0]
        return ll

    def getThumbnails(self):
        return getattr(self, 'thumbnails', [])

    def loadPageAtIndex(self, index, sortby):
        if self.currentPage == index:
            if self.sortType is None: return
            if self.sortType == sortby: return

        if self.sortType is None:
            if self.searchKeyWord is None:
                newUrl = self.url + f"?from={index+1}"
            else:
                newUrl = f"{self._url_root}/search/?q={self.searchKeyWord}&from={index+1}"
        else:
            if self.searchKeyWord is None:
                newUrl = self.url + f"?sort_by={JableTVList._sortby_dict[sortby]}&from={index+1}"
            else:
                newUrl = f"{self._url_root}/search/?q={self.searchKeyWord}&sort_by={JableTVList._sortby_dict[sortby]}&from={index+1}"
        self._url_get(newUrl)
        self.currentPage = index
        self.sortType = sortby


class JableTVBrowser:
    """Fetches categories and video listings from jable.tv for the browse GUI."""
    _url_root = 'https://jable.tv'
    _scraper = None

    # ── Homepage sections (prepended to category list) ───────────────────
    HOMEPAGE_SECTIONS = [
        ('最近更新', 'https://jable.tv/latest-updates/'),
        ('熱門影片', 'https://jable.tv/hot/'),
        ('新片上架', 'https://jable.tv/new-release/'),
    ]

    # ── Hot section time filters ─────────────────────────────────────────
    HOT_TIME_FILTERS = {
        '所有時間': '',
        '今日熱門': 't=today',
        '本週熱門': 't=week',
        '本月熱門': 't=month',
    }

    # ── Sidebar tags (mirrors jable.tv left sidebar) ─────────────────────
    SIDEBAR_TAGS = {
        '衣著': [
            ('黑絲', 'black-pantyhose'), ('過膝襪', 'knee-socks'),
            ('運動裝', 'sportswear'), ('肉絲', 'flesh-toned-pantyhose'),
            ('絲襪', 'pantyhose'), ('眼鏡娘', 'glasses'),
            ('獸耳', 'kemonomimi'), ('漁網', 'fishnets'),
            ('水着', 'swimsuit'), ('校服', 'school-uniform'),
            ('旗袍', 'cheongsam'), ('婚紗', 'wedding-dress'),
            ('女僕', 'maid'), ('和服', 'kimono'),
            ('吊帶襪', 'stockings'), ('兔女郎', 'bunny-girl'),
            ('Cosplay', 'Cosplay'),
        ],
        '身材': [
            ('黑肉', 'suntan'), ('長身', 'tall'),
            ('軟體', 'flexible-body'), ('貧乳', 'small-tits'),
            ('美腿', 'beautiful-leg'), ('美尻', 'beautiful-butt'),
            ('紋身', 'tattoo'), ('短髮', 'short-hair'),
            ('白虎', 'hairless-pussy'), ('熟女', 'mature-woman'),
            ('巨乳', 'big-tits'), ('少女', 'girl'),
            ('嬌小', 'dainty'),
        ],
        '交合': [
            ('顏射', 'facial'), ('腳交', 'footjob'),
            ('肛交', 'anal-sex'), ('痙攣', 'spasms'),
            ('潮吹', 'squirting'), ('深喉', 'deep-throat'),
            ('接吻', 'kiss'), ('口爆', 'cum-in-mouth'),
            ('口交', 'blowjob'), ('乳交', 'tit-wank'),
            ('中出', 'creampie'),
        ],
        '玩法': [
            ('露出', 'outdoor'), ('集團進犯', 'gang-intrusion'),
            ('進犯', 'intrusion'), ('調教', 'tune'),
            ('綑綁', 'bondage'), ('瞬間插入', 'quickie'),
            ('痴漢', 'chikan'), ('痴女', 'chizyo'),
            ('男M', 'masochism-guy'), ('泥醉', 'crapulence'),
            ('泡姬', 'soapland'), ('母乳', 'breast-milk'),
            ('放尿', 'piss'), ('按摩', 'massage'),
            ('多P', 'groupsex'), ('刑具', 'grip'),
            ('凌辱', 'insult'), ('一日十回', '10-times-a-day'),
            ('3P', '3p'),
        ],
        '劇情': [
            ('黑人', 'black'), ('醜男', 'ugly-man'),
            ('誘惑', 'temptation'), ('親屬', 'kinship'),
            ('童貞', 'virginity'), ('時間停止', 'time-stop'),
            ('復仇', 'avenge'), ('年齡差', 'age-difference'),
            ('巨漢', 'giant'), ('媚藥', 'love-potion'),
            ('夫目前犯', 'sex-beside-husband'), ('出軌', 'affair'),
            ('催眠', 'hypnosis'), ('偷拍', 'private-cam'),
            ('下雨天', 'rainy-day'), ('NTR', 'ntr'),
        ],
        '角色': [
            ('風俗娘', 'club-hostess-and-sex-worker'), ('醫生', 'doctor'),
            ('逃犯', 'fugitive'), ('護士', 'nurse'),
            ('老師', 'teacher'), ('空姐', 'flight-attendant'),
            ('球隊經理', 'team-manager'), ('未亡人', 'widow'),
            ('搜查官', 'detective'), ('情侶', 'couple'),
            ('家政婦', 'housewife'), ('家庭教師', 'private-teacher'),
            ('偶像', 'idol'), ('人妻', 'wife'),
            ('主播', 'female-anchor'), ('OL', 'ol'),
        ],
        '地點': [
            ('魔鏡號', 'magic-mirror'), ('電車', 'tram'),
            ('處女', 'first-night'), ('監獄', 'prison'),
            ('溫泉', 'hot-spring'), ('洗浴場', 'bathing-place'),
            ('泳池', 'swimming-pool'), ('汽車', 'car'),
            ('廁所', 'toilet'), ('學校', 'school'),
            ('圖書館', 'library'), ('健身房', 'gym-room'),
            ('便利店', 'store'),
        ],
        '雜項': [
            ('錄像', 'video-recording'), ('處女作/引退作', 'debut-retires'),
            ('綜藝', 'variety-show'), ('節日主題', 'festival'),
            ('感謝祭', 'thanksgiving'), ('4小時以上', 'more-than-4-hours'),
        ],
    }

    @classmethod
    def _get_scraper(cls):
        global _browser_scraper
        if _browser_scraper is None:
            with _browser_scraper_lock:
                if _browser_scraper is None:
                    _browser_scraper = _make_scraper()
        cls._scraper = _browser_scraper
        return _browser_scraper

    @classmethod
    def tag_url(cls, slug):
        return f'{cls._url_root}/tags/{slug}/'

    @classmethod
    def fetch_categories(cls):
        """Return homepage sections + dynamic categories from /categories/."""
        cats = [{'name': site_i18n.loc(site_i18n.CATEGORY_I18N, url, name),
                 'url': url, 'count': 0, 'section': True}
                for name, url in cls.HOMEPAGE_SECTIONS]
        try:
            def _validate(resp):
                s = BeautifulSoup(resp.content, 'html.parser')
                return bool(s.select('a[href*="/categories/"]'))
            scr = cls._get_scraper()
            _apply_jable_lang(scr)
            r, host, reason = fetch_with_mirrors(scr, f'{cls._url_root}/categories/', 'jable', _validate, timeout=30)
            if reason != 'ok':
                return cats
            soup = BeautifulSoup(r.content, 'html.parser')
            for a in soup.select('a[href*="/categories/"]'):
                href = a.get('href', '')
                text = a.get_text(strip=True)
                if '/categories/' in href and href != f'{cls._url_root}/categories/' and text:
                    name = text
                    count_match = re.search(
                        r'(\d[\d,]*)\s*(?:部影片|videos?)', text, re.I)
                    count = int(count_match.group(1).replace(',', '')) if count_match else 0
                    name = re.sub(
                        r'\d[\d,]*\s*(?:部影片|videos?)', '', name,
                        flags=re.I).strip()
                    slug = href.rstrip('/').split('/')[-1]
                    cats.append({'name': site_i18n.loc(site_i18n.CATEGORY_I18N, href, name),
                                 'slug': slug, 'url': href, 'count': count})
        except Exception:
            pass
        return cats

    @classmethod
    def fetch_page(cls, url):
        def _validate(resp):
            s = BeautifulSoup(resp.content, 'html.parser')
            dl = s.find('div', id=lambda x: x and x.startswith('list_videos'))
            return bool(dl and dl.select('div.video-img-box'))
        scr = cls._get_scraper()
        _apply_jable_lang(scr)
        resp, host, reason = fetch_with_mirrors(scr, url, 'jable', _validate)
        if reason != 'ok':
            if reason == 'blocked':
                raise MirrorsBlockedError(url)
            return []
        try:
            soup = BeautifulSoup(resp.content, 'html.parser')
            divlist = soup.find('div', id=lambda x: x and x.startswith('list_videos'))
            if divlist is None: return []
            cards = divlist.select('div.video-img-box')
            videos = []
            for card in cards:
                detail = card.select_one('div.detail')
                if not detail or not detail.h6 or not detail.h6.a: continue
                tag_a = detail.h6.a
                img = card.select_one('img')
                duration_span = card.select_one('span.label')
                videos.append({
                    'url': tag_a.get('href', ''),
                    'title': str(tag_a.string or ''),
                    'thumbnail': img.get('data-src', '') if img else '',
                    'duration': duration_span.string if duration_span else '',
                })
            return videos
        except Exception:
            return []

    @classmethod
    def fetch_sidebar_tags(cls):
        """Return the sidebar tag structure for the browse UI."""
        result = {}
        for group, tags in cls.SIDEBAR_TAGS.items():
            result[group] = [{'name': name, 'slug': slug,
                              'url': cls.tag_url(slug)} for name, slug in tags]
        return result
