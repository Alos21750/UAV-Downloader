"""Shared listing adapters for desktop frontends."""
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
import re

from uav_downloader.sites.jabletv import JableTVBrowser
from uav_downloader.sites.missav import MissAVBrowser
from uav_downloader.sites.supjav import SupJavBrowser
from uav_downloader.sites.hanime1 import Hanime1Browser
from uav_downloader.sites.huangguo import HuangguoBrowser

BROWSERS = dict(JableTV=JableTVBrowser, MissAV=MissAVBrowser,
                SupJav=SupJavBrowser, Hanime1=Hanime1Browser, Huangguo=HuangguoBrowser)


def video_badge(url, title):
    """Original display heuristic; never used as trusted subtitle evidence."""
    url, title = str(url or '').lower(), str(title or '').lower()
    path = url.split('?', 1)[0].rstrip('/')
    if 'uncensored-leak' in url or any(value in title for value in ('無碼', '无码', 'uncensored')):
        return 'uncensored'
    if 'chinese-subtitle' in url or path.endswith('-c') or '-c/' in url or any(value in title for value in ('中文字幕', '中字')):
        return 'chinese-subtitle'
    return ''


def listing_url(site, base, query, page=1, language=''):
    browser = BROWSERS[site]
    page = max(1, int(page))
    if query:
        if site == 'JableTV':
            base = 'https://jable.tv/search/?q=' + quote(query, safe='')
        elif site == 'MissAV':
            prefix = f'{language}/' if language else ''
            base = f'https://missav.ai/{prefix}search/{quote(query, safe="")}'
        elif site == 'SupJav':
            base = browser.search_url(query, lang=language)
        else:
            base = browser.search_url(query)
    if site == 'JableTV':
        parts = urlsplit(base)
        pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'from']
        pairs.append(('from', str(page)))
        # Keep search quoting compatible with the browser's existing contract.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs, quote_via=quote), ''))
    if site == 'MissAV':
        parts = urlsplit(base)
        pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'page']
        if page > 1:
            pairs.append(('page', str(page)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs, quote_via=quote), ''))
    return browser.page_url(base, page)


def fetch_listing(site, base='', query='', page=1, language='', *, cancel_check=None):
    browser = BROWSERS[site]
    categories = (browser.fetch_categories(lang=language)
                  if site in {'MissAV', 'SupJav'} else browser.fetch_categories())
    if not base and not query:
        if not categories:
            raise ValueError('No browse categories were returned')
        base = categories[0]['url']
    url = listing_url(site, base, query, page, language)
    videos = (browser.fetch_page(url, cancel_check=cancel_check)
              if site == 'Huangguo' else browser.fetch_page(url))
    return dict(categories=categories, base=base, url=url, videos=[
        dict(video, _site=site, _source_listing_url=url) for video in videos])


def hanime_filter_url(values, catalog=None):
    values = dict(values)
    allowed = {'query', 'genre', 'sort', 'date', 'duration', 'tags'}
    if values.keys() - allowed:
        raise ValueError('Unknown Hanime1 filter')
    catalog = catalog or Hanime1Browser.filter_catalog()
    for field, options in [('genre', 'genres'), ('sort', 'sorts'), ('date', 'dates'), ('duration', 'durations')]:
        if values.get(field) and values[field] not in catalog[options]:
            raise ValueError(f'Unknown Hanime1 {field}')
    tags = values.get('tags', [])
    if not isinstance(tags, (list, tuple)) or any(tag not in catalog['tags'] for tag in tags):
        raise ValueError('Unknown Hanime1 tag')
    return Hanime1Browser.filter_url(**values)


def listing_site(url):
    """Recognize supported listings without treating language-prefixed feeds as videos."""
    from uav_downloader import sites
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or '').casefold().removeprefix('www.')
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
            return None
    except ValueError:
        return None
    if host in {'jable.tv', 'fs1.app'}:
        return None if parts.path.startswith('/videos/') else 'JableTV'
    if host == 'supjav.com':
        return None if re.fullmatch(r'/(?:(?:zh|ja)/)?\d+\.html', parts.path) else 'SupJav'
    if host == 'hanime1.me':
        return 'Hanime1' if parts.path.rstrip('/') in {'', '/search'} else None
    if host == 'huangguoai.com':
        return 'Huangguo' if HuangguoBrowser.is_listing_url(url) else None
    if host in {'missav.ai', 'missav.ws', 'missav.live', 'missav123.com'}:
        path = [part for part in parts.path.split('/') if part and
                not re.fullmatch(r'dm\d+|cn|en|ja|ko|ms|th', part)]
        feeds = {urlsplit(value).path.rsplit('/', 1)[-1] for _, value in MissAVBrowser.CATEGORIES}
        if not path or path[0] in feeds | {'search', 'actresses', 'actors', 'makers', 'genres', 'tags'}:
            return 'MissAV'
        return None if sites.VaildateUrl(url) else 'MissAV'
    return None


def fetch_listing_page(site, base, page):
    url = listing_url(site, base, '', page)
    return [dict(video, _site=site, _source_listing_url=url) for video in BROWSERS[site].fetch_page(url)]
