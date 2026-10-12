#!/usr/bin/env python
# coding: utf-8
"""Cross-site video identity and version classification for UAV Watcher."""

from collections import defaultdict
import re
from urllib.parse import parse_qs, unquote, urlsplit


DEFAULT_VERSION_PREFERENCE = 'chinese-subtitle'
VALID_VERSION_PREFERENCES = frozenset({
    'chinese-subtitle',
    'uncensored',
    'standard',
    'english-subtitle',
    'reducing-mosaic',
})

SOURCE_PRIORITY = {
    'JableTV': 0,
    'MissAV': 1,
    'SupJav': 2,
    'Hanime1': 3,
}

# These tokens are deliberately closed and small because they are persisted by
# the UAV Browser queue. They mean that the source version itself already carries
# Chinese subtitles (normally burned into the picture), not merely that a
# Chinese-language site UI or an unrelated title string was observed.
TRUSTED_CHINESE_SUBTITLE_EVIDENCE = frozenset({
    'jable-category-chinese-subtitle',
    'missav-category-chinese-subtitle',
    'missav-url-chinese-subtitle',
    'supjav-category-chinese-subtitle',
    'hanime1-tag-chinese-subtitle',
})
MAX_SOURCE_SUBTITLE_EVIDENCE_TEXT = 512
_TRUSTED_MISSAV_HOSTS = frozenset({
    'missav.ai', 'www.missav.ai',
    'missav.ws', 'www.missav.ws',
    'missav.live', 'www.missav.live',
    'missav123.com', 'www.missav123.com',
})
_TRUSTED_JABLE_HOSTS = frozenset({
    'jable.tv', 'www.jable.tv',
    'fs1.app', 'www.fs1.app',
})
_TRUSTED_SUPJAV_HOSTS = frozenset({
    'supjav.com', 'www.supjav.com',
})
_TRUSTED_HANIME1_HOSTS = frozenset({
    'hanime1.me', 'www.hanime1.me',
})

_TARGET_VERSION = {
    'category:chinese-subtitle': 'chinese-subtitle',
    'category:chinese-subtitles': 'chinese-subtitle',
    'feed:chinese-subtitle': 'chinese-subtitle',
    'category:english-subtitles': 'english-subtitle',
    'category:uncensored': 'uncensored',
    'feed:uncensored-leak': 'uncensored',
    'category:reducing-mosaic': 'reducing-mosaic',
    'category:censored': 'standard',
    'tag:chinese-subtitle': 'chinese-subtitle',
    'tag:uncensored': 'uncensored',
}

_VERSION_SUFFIXES = (
    '-uncensored-leak',
    '-chinese-subtitle',
    '-chinese-subtitles',
    '-english-subtitle',
    '-english-subtitles',
    '-reducing-mosaic',
    '-reduced-mosaic',
)


def site_from_url(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or '').casefold()
    except (TypeError, ValueError):
        return ''
    if host == 'jable.tv' or host.endswith('.jable.tv'):
        return 'JableTV'
    if host == 'fs1.app' or host.endswith('.fs1.app'):
        return 'JableTV'
    if (host.startswith('missav.') or host == 'missav123.com' or
            host.endswith('.missav123.com')):
        return 'MissAV'
    if host == 'supjav.com' or host.endswith('.supjav.com'):
        return 'SupJav'
    if host == 'hanime1.me' or host.endswith('.hanime1.me'):
        return 'Hanime1'
    return ''


def normalize_source_subtitle_evidence(value) -> tuple[str, ...]:
    """Return only bounded, reviewed source-subtitle evidence tokens.

    Queue CSV files and UAV Watcher state are user-writable local data. Unknown
    or oversized values therefore fail closed instead of becoming a reason to
    skip subtitle work.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        if len(value) > MAX_SOURCE_SUBTITLE_EVIDENCE_TEXT:
            return ()
        values = value.split('|')
        if len(values) > len(TRUSTED_CHINESE_SUBTITLE_EVIDENCE):
            return ()
    elif isinstance(value, (list, tuple, set, frozenset)):
        if len(value) > len(TRUSTED_CHINESE_SUBTITLE_EVIDENCE):
            return ()
        values = value
    else:
        return ()

    normalized = {
        str(item).strip()
        for item in values
        if isinstance(item, str)
        and len(item) <= MAX_SOURCE_SUBTITLE_EVIDENCE_TEXT
        and str(item).strip() in TRUSTED_CHINESE_SUBTITLE_EVIDENCE
    }
    return tuple(sorted(normalized))


def _trusted_source_site(url: str) -> str:
    try:
        parsed = urlsplit(str(url or ''))
        scheme = (parsed.scheme or '').casefold()
        host = (parsed.hostname or '').casefold()
    except (TypeError, ValueError):
        return ''
    if scheme != 'https' or parsed.username or parsed.password:
        return ''
    if host in _TRUSTED_JABLE_HOSTS:
        return 'JableTV'
    if host in _TRUSTED_MISSAV_HOSTS:
        return 'MissAV'
    if host in _TRUSTED_SUPJAV_HOSTS:
        return 'SupJav'
    if host in _TRUSTED_HANIME1_HOSTS:
        return 'Hanime1'
    return ''


def _trusted_listing_evidence(site: str, listing_url: str) -> str:
    try:
        parsed = urlsplit(str(listing_url or ''))
        scheme = (parsed.scheme or '').casefold()
        host = (parsed.hostname or '').casefold()
        path = unquote(parsed.path or '').casefold()
    except (TypeError, ValueError):
        return ''
    if scheme != 'https' or parsed.username or parsed.password:
        return ''

    if site == 'JableTV':
        if host not in _TRUSTED_JABLE_HOSTS:
            return ''
        if path.rstrip('/') == '/categories/chinese-subtitle':
            return 'jable-category-chinese-subtitle'
        return ''

    if site == 'MissAV':
        if host not in _TRUSTED_MISSAV_HOSTS:
            return ''
        if re.fullmatch(
                r'/dm\d+/(?:(?:cn|en|ja|ko|ms|th)/)?'
                r'chinese-subtitle/?',
                path):
            return 'missav-category-chinese-subtitle'
        return ''

    if site == 'SupJav':
        if host not in _TRUSTED_SUPJAV_HOSTS:
            return ''
        if re.fullmatch(
                r'/(?:(?:zh|ja)/)?category/chinese-subtitles/?',
                path):
            return 'supjav-category-chinese-subtitle'
        return ''

    if site == 'Hanime1':
        if host not in _TRUSTED_HANIME1_HOSTS or path.rstrip('/') != '/search':
            return ''
        tags = parse_qs(parsed.query, keep_blank_values=True).get('tags[]', [])
        if '中文字幕' in tags:
            return 'hanime1-tag-chinese-subtitle'
    return ''


def trusted_chinese_subtitle_evidence(video: dict) -> tuple[str, ...]:
    """Collect fail-closed evidence that a source version carries Chinese text.

    This intentionally does not call :func:`video_versions`: that broader
    classifier accepts display-title heuristics for browsing and deduplication,
    which are too weak to suppress requested subtitle generation.
    """
    if not isinstance(video, dict):
        return ()

    stored_evidence = normalize_source_subtitle_evidence(
        video.get('_source_subtitle_evidence')
        or video.get('source_subtitle_evidence'))
    evidence = set()
    url = str(video.get('url') or '')
    derived_site = _trusted_source_site(url)
    claimed_site = str(video.get('_site') or video.get('site') or '').strip()
    if claimed_site not in SOURCE_PRIORITY:
        claimed_site = ''
    # Evidence is useful only when it is attached to a concrete, trusted
    # source video URL.  Metadata tokens alone never authorize skipping work.
    if not derived_site:
        return ()
    # Conflicting site metadata is not trusted for category evidence.
    site = derived_site
    site_consistent = not claimed_site or derived_site == claimed_site
    evidence_for_site = {
        'JableTV': {'jable-category-chinese-subtitle'},
        'MissAV': {
            'missav-category-chinese-subtitle',
            'missav-url-chinese-subtitle',
        },
        'SupJav': {'supjav-category-chinese-subtitle'},
        'Hanime1': {'hanime1-tag-chinese-subtitle'},
    }.get(site, set())
    if site_consistent:
        evidence.update(
            token for token in stored_evidence
            if token in evidence_for_site)

    if derived_site == 'MissAV':
        try:
            source_host = (urlsplit(url).hostname or '').casefold()
        except (TypeError, ValueError):
            source_host = ''
        if source_host in _TRUSTED_MISSAV_HOSTS:
            slug = url_slug(url)
            if slug.endswith(('-chinese-subtitle', '-chinese-subtitles')):
                evidence.add('missav-url-chinese-subtitle')

    target_id = str(video.get('_target_id') or '')
    target_evidence = {
        ('JableTV', 'category:chinese-subtitle'):
            'jable-category-chinese-subtitle',
        ('MissAV', 'feed:chinese-subtitle'):
            'missav-category-chinese-subtitle',
        ('SupJav', 'category:chinese-subtitles'):
            'supjav-category-chinese-subtitle',
    }.get((site, target_id))
    if site_consistent and target_evidence:
        evidence.add(target_evidence)

    listing_evidence = _trusted_listing_evidence(
        site, video.get('_source_listing_url')
        or video.get('source_listing_url'))
    if site_consistent and listing_evidence:
        evidence.add(listing_evidence)

    return normalize_source_subtitle_evidence(evidence)


def url_slug(url: str) -> str:
    try:
        path = unquote(urlsplit(url).path).rstrip('/')
        return path.rsplit('/', 1)[-1].casefold()
    except (AttributeError, TypeError, ValueError):
        return ''


def _strip_version_suffixes(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for suffix in _VERSION_SUFFIXES:
            if text.endswith(suffix):
                text = text[:-len(suffix)]
                changed = True
                break
    return text


def canonical_code(text: str) -> str:
    """Extract a stable JAV code from a URL slug or listing title."""
    value = unquote(str(text or '')).casefold()
    value = _strip_version_suffixes(value.strip().rstrip('/'))

    match = re.search(
        r'(?<![a-z0-9])fc2\s*[-_ ]?\s*ppv\s*[-_ ]*(\d{4,9})(?!\d)',
        value, re.I)
    if match:
        return f'fc2-ppv-{match.group(1)}'

    match = re.search(
        r'(?<![a-z0-9])fc2\s*[-_ ]+(\d{4,9})(?!\d)', value, re.I)
    if match:
        return f'fc2-{match.group(1)}'

    match = re.search(
        r'(?<![a-z0-9])([a-z0-9]{1,15})[-_ ]+(\d{6})[-_](\d{3,4})(?!\d)',
        value, re.I)
    if match and re.search(r'[a-z]', match.group(1), re.I):
        return (f'{match.group(1)}-{match.group(2)}-{match.group(3)}'
                .casefold())

    match = re.search(r'(?<!\d)(\d{6})[-_](\d{3,4})(?!\d)', value)
    if match:
        return f'{match.group(1)}-{match.group(2)}'

    match = re.search(
        r'(?<![a-z0-9])([a-z0-9]{1,10}(?:-[a-z0-9]{1,10}){0,2})'
        r'[-_]+(\d{2,9})(?!\d)',
        value, re.I)
    if match and re.search(r'[a-z]', match.group(1), re.I):
        prefix = match.group(1).replace('_', '-').casefold()
        return f'{prefix}-{match.group(2)}'
    return ''


def video_code(video: dict) -> str:
    stored = str(video.get('_code') or video.get('code') or '').strip()
    if stored:
        return stored.casefold()

    site = video.get('_site') or video.get('site') or site_from_url(
        video.get('url', ''))
    if site in {'JableTV', 'MissAV'}:
        slug = _strip_version_suffixes(url_slug(video.get('url', '')))
        code = canonical_code(slug)
        if code:
            return code

    code = canonical_code(video.get('title', ''))
    if code:
        return code
    return ''


def video_versions(video: dict) -> set[str]:
    stored = video.get('_versions') or video.get('versions')
    if isinstance(stored, str) and stored in VALID_VERSION_PREFERENCES:
        return {stored}
    if isinstance(stored, (list, tuple, set, frozenset)):
        valid = {str(item) for item in stored
                 if str(item) in VALID_VERSION_PREFERENCES}
        if valid:
            return valid

    versions = set()
    target_version = _TARGET_VERSION.get(str(video.get('_target_id') or ''))
    if target_version:
        versions.add(target_version)

    slug = url_slug(video.get('url', ''))
    if slug.endswith('-uncensored-leak'):
        versions.add('uncensored')
    if slug.endswith(('-chinese-subtitle', '-chinese-subtitles')):
        versions.add('chinese-subtitle')
    if slug.endswith(('-english-subtitle', '-english-subtitles')):
        versions.add('english-subtitle')
    if slug.endswith(('-reducing-mosaic', '-reduced-mosaic')):
        versions.add('reducing-mosaic')

    title = str(video.get('title') or '').casefold()
    if ('chinese subtitle' in title or '中文字幕' in title or
            re.search(r'\[\s*中字\s*\]', title)):
        versions.add('chinese-subtitle')
    if 'english subtitle' in title or '英文字幕' in title:
        versions.add('english-subtitle')
    if ('reducing mosaic' in title or 'reduced mosaic' in title or
            'mosaic reduced' in title or
            '破壞版' in title or '破坏版' in title):
        versions.add('reducing-mosaic')
    if ('uncensored' in title or 'uncensored leak' in title or
            '無碼' in title or '无码' in title or
            slug.endswith('-uncensored-leak')):
        versions.add('uncensored')

    if not versions:
        versions.add('standard')
    return versions


def normalize_version_preference(value) -> str:
    pref = str(value or '').strip().casefold()
    if pref == 'all':
        return pref
    if pref == 'uncensored-leak':
        pref = 'uncensored'
    if pref in VALID_VERSION_PREFERENCES:
        return pref
    return DEFAULT_VERSION_PREFERENCE


def dedupe_video_candidates(videos: list[dict], preference: str):
    """Deduplicate by code, or by code+variant for all-version downloads."""
    preference = normalize_version_preference(preference)
    url_versions = defaultdict(set)
    url_source_subtitle_evidence = defaultdict(set)
    records = []

    for index, video in enumerate(videos):
        url = str(video.get('url') or '').strip().casefold()
        versions = video_versions(video)
        if url:
            url_versions[url].update(versions)
            url_source_subtitle_evidence[url].update(
                trusted_chinese_subtitle_evidence(video))
        records.append((index, video, url, video_code(video), versions))

    kept = []
    kept_records = []
    identity_indexes = {}
    decisions = []

    for index, video, url, code, versions in records:
        if url:
            versions = set(url_versions[url])
            if len(versions) > 1:
                versions.discard('standard')
            video['_versions'] = sorted(versions)
            source_evidence = normalize_source_subtitle_evidence(
                url_source_subtitle_evidence[url])
            if source_evidence:
                # The same concrete source URL can appear in multiple official
                # listings. Preserve only the dedicated trusted evidence; the
                # broader `_versions` classifier is never used as this gate.
                video['_source_subtitle_evidence'] = source_evidence
            else:
                video.pop('_source_subtitle_evidence', None)
        if code:
            video['_code'] = code
        identity = f'code:{code}' if code else (f'url:{url}' if url else '')
        if code and preference == 'all':
            identity += ':versions:' + '+'.join(sorted(versions))
        if not identity:
            kept.append(video)
            kept_records.append((index, video, url, code, versions))
            continue

        existing_index = identity_indexes.get(identity)
        if existing_index is None:
            identity_indexes[identity] = len(kept)
            kept.append(video)
            kept_records.append((index, video, url, code, versions))
            continue

        existing_record = kept_records[existing_index]
        existing_video = existing_record[1]
        existing_versions = existing_record[4]

        candidate_score = (
            0 if preference in versions else 1,
            0 if video.get('_already_seen') else 1,
            SOURCE_PRIORITY.get(video.get('_site'), 99),
            index,
        )
        existing_score = (
            0 if preference in existing_versions else 1,
            0 if existing_video.get('_already_seen') else 1,
            SOURCE_PRIORITY.get(existing_video.get('_site'), 99),
            existing_record[0],
        )
        if candidate_score < existing_score:
            kept[existing_index] = video
            kept_records[existing_index] = (
                index, video, url, code, versions)
            decisions.append((existing_video, video, code or url))
        else:
            decisions.append((video, existing_video, code or url))

    return kept, decisions
