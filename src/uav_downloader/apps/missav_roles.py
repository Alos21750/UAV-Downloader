"""Match user-selected Watcher roles against already fetched MissAV titles.

These are local title keywords, not a claim that the provider tagged the video.
IDs match the existing localized Jable role labels, including its historical
`housewife` ID for a housekeeper. Never match the general term 'wife'.
"""
from itertools import islice
import re
import unicodedata


ROLE_TERMS = {
    'fugitive': ('逃犯', '逃亡者', '通緝犯', '通缉犯', 'fugitive', 'fugitives', 'ผู้หลบหนี'),
    'nurse': ('護士', '护士', '護理師', '护理师', '看護師', '看護婦', 'ナース',
              'nurse', 'nurses', 'พยาบาล'),
    'teacher': ('老師', '老师', '教師', '教师', '教員', 'teacher', 'teachers', 'ครู'),
    'flight-attendant': ('空姐', '空服員', '空服员', '客室乗務員', 'キャビンアテンダント',
                         'flight attendant', 'flight attendants', 'stewardess', 'แอร์โฮสเตส'),
    'team-manager': ('球隊經理', '球队经理', '部活マネージャー', '部活のマネージャー',
                     'チームマネージャー', 'team manager', 'team managers', 'ผู้จัดการทีม'),
    'widow': ('未亡人', '寡婦', '寡妇', 'widow', 'widows', 'หญิงม่าย', 'แม่ม่าย'),
    'detective': ('搜查官', '捜査官', '搜查員', '搜查员', '捜査員', '偵探', '侦探', '探偵',
                  'investigator', 'investigators', 'detective', 'detectives', 'นักสืบ'),
    'couple': ('情侶', '情侣', 'カップル', 'couple', 'couples', 'คู่รัก'),
    'housewife': ('家政婦', '家政妇', '家政阿姨', '家事代行', 'housekeeper', 'housekeepers',
                  'แม่บ้านทำความสะอาด'),
    'private-teacher': ('家庭教師', '家庭教师', '私人教師', '私人教师', '私人老師', '私人老师',
                        '補習老師', '补习老师', 'private teacher', 'private teachers',
                        'private tutor', 'private tutors', 'tutor', 'tutors', 'ครูสอนพิเศษ', 'ติวเตอร์'),
    'idol': ('偶像', 'アイドル', 'idol', 'idols', 'ไอดอล'),
    'female-anchor': ('主播', '女子アナ', '女性アナウンサー', 'female anchor', 'female anchors',
                      'female announcer', 'female announcers', 'anchorwoman', 'anchorwomen',
                      'news anchor', 'news anchors', 'ผู้ประกาศข่าวหญิง'),
    'ol': ('OL', 'オフィスレディ', 'office lady', 'office ladies', 'สาวออฟฟิศ'),
}


def _fold(text):
    return ' '.join(unicodedata.normalize('NFKC', text[:2048]).casefold()[:4096].split())


def _compile(aliases):
    patterns = []
    for alias in sorted(aliases, key=len, reverse=True):
        term = _fold(alias)
        expr = re.escape(term)
        if term.isascii():
            # Plain \b misses “ＯＬの…” because Han/Kana are word characters
            # too. Constrain the Latin token only, not adjacent Japanese.
            expr = expr.replace(r'\ ', r'[ \-‐‑]')
            expr = r'(?<![a-z0-9_])' + expr + r'(?![a-z0-9_])'
        patterns.append(expr)
    return re.compile('|'.join(patterns))


_PATTERNS = {role: _compile(aliases) for role, aliases in ROLE_TERMS.items()}
_EXCLUDE = {
    'teacher': _PATTERNS['private-teacher'],
    'idol': re.compile('偶像[劇剧]'),
    'nurse': re.compile('โรงพยาบาล'),  # hospital, not a nurse
    'couple': re.compile(r'(?<![a-z0-9_])couple of(?![a-z0-9_])'),
    'ol': re.compile(r'(?<![a-z0-9_])ol[-‐‑][0-9]+(?![a-z0-9_])'),
}


def matched_role_ids(title, selected_slugs):
    """Return unique matching role IDs in the caller's selection order."""
    if not isinstance(title, str) or not title or selected_slugs is None:
        return ()
    text = _fold(title)
    result = []
    if isinstance(selected_slugs, str):
        selected_slugs = (selected_slugs,)
    try:
        selected = iter(selected_slugs)
    except TypeError:
        return ()
    seen = set()
    for slug in islice(selected, 128):
        if not isinstance(slug, str) or len(slug) > 32 or slug in seen or slug not in _PATTERNS:
            continue
        seen.add(slug)
        candidate = _EXCLUDE[slug].sub(' ', text) if slug in _EXCLUDE else text
        if _PATTERNS[slug].search(candidate):
            result.append(slug)
    return tuple(result)
