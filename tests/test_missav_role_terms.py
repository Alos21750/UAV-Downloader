"""Offline title heuristics: affirmative matches, boundaries and stable selection."""
import pytest

from uav_downloader.apps.missav_roles import ROLE_TERMS, matched_role_ids


CASES = {
    'fugitive': ('逃犯', '逃亡者', 'Fugitive', 'ผู้หลบหนี'),
    'nurse': ('護士', '护士', 'ナース', 'Nurses', 'พยาบาล'),
    'teacher': ('老師', '老师', '女教師', 'Teacher', 'คุณครู'),
    'flight-attendant': ('空姐', '客室乗務員', 'Flight Attendant', 'แอร์โฮสเตส'),
    'team-manager': ('球隊經理', '球队经理', '部活マネージャー', 'Team Manager', 'ผู้จัดการทีม'),
    'widow': ('未亡人', '寡妇', 'Widow', 'หญิงม่าย'),
    'detective': ('搜查官', '捜査官', 'Investigator', 'นักสืบ'),
    'couple': ('情侶', '情侣', 'カップル', 'Couple', 'คู่รัก'),
    'housewife': ('家政婦', '家政妇', 'Housekeeper', 'แม่บ้านทำความสะอาด'),
    'private-teacher': ('家庭教師', '家庭教师', 'Private Teacher', 'Tutor', 'ครูสอนพิเศษ'),
    'idol': ('偶像', 'アイドル', 'Idol', 'ไอดอล'),
    'female-anchor': ('主播', '女子アナ', 'Female Announcer', 'ผู้ประกาศข่าวหญิง'),
    'ol': ('ＯＬ', 'Office Lady', 'オフィスレディ', 'สาวออฟฟิศ'),
}


def test_catalog_and_match_each_translation():
    assert tuple(ROLE_TERMS) == tuple(CASES)
    assert all(isinstance(terms, tuple) for terms in ROLE_TERMS.values())
    for role, titles in CASES.items():
        for title in titles:
            assert matched_role_ids(title, ROLE_TERMS) == (role,), (role, title)


@pytest.mark.parametrize('title', [
    'school nursemaid idolized teacherly anchorless manifold',
    '家政學／家庭主婦／家庭主妇／人妻／Housewife',
    'taxi driver and manager in CA', 'OLIVE oil COLOUR',
    'hotel managership; a couple of days', '偶像劇的片尾／偶像剧片头',
    'โรงพยาบาล', 'OL-123',
])
def test_unrelated_text_not_a_role(title):
    assert matched_role_ids(title, ROLE_TERMS) == ()


def test_selection_preserves_order_no_duplicates_and_no_private_teacher_double_count():
    title = 'ＯＬ與家庭教師：PRIVATE   TEACHER、偶像'
    assert matched_role_ids(title, ('idol', 'ol', 'teacher', 'ol', 'private-teacher')) == (
        'idol', 'ol', 'private-teacher')
    assert not matched_role_ids('家庭教師', ['teacher'])
    assert matched_role_ids('家庭教師與老師', ['teacher', 'private-teacher']) == (
        'teacher', 'private-teacher')
    assert matched_role_ids('former tutor and school teacher', ['teacher']) == ('teacher',)


def test_bad_and_oversized_inputs_are_bounded():
    assert not matched_role_ids(None, ['ol'])
    assert not matched_role_ids('OL', None)
    assert not matched_role_ids('OL', 123)
    assert not matched_role_ids('nurse', ['unknown', None, []])
    assert not matched_role_ids(('Nurse',), ['nurse'])
    assert not matched_role_ids('x' * 20_000 + ' OL', ['ol'])
    assert matched_role_ids('新入社員ＯＬの日常', ['ol']) == ('ol',)
