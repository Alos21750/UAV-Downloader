from uav_downloader.core import browse_service


def test_listing_url_preserves_search_and_pagination():
    assert browse_service.listing_url('JableTV', '', 'a & b', 2) == (
        'https://jable.tv/search/?q=a%20%26%20b&from=2')
    assert browse_service.listing_url('MissAV', '', 'a/b', 3, 'en') == (
        'https://missav.ai/en/search/a%2Fb?page=3')
    assert browse_service.listing_url('SupJav', 'https://supjav.com/category/chinese-subtitles', '', 2).endswith('/page/2')


def test_fetch_listing_uses_localized_category_and_keeps_evidence(monkeypatch):
    class Browser:
        @staticmethod
        def fetch_categories(lang):
            assert lang == 'en'
            return [dict(name='Subtitle', url='https://missav.ai/dm296/en/chinese-subtitle')]
        @staticmethod
        def page_url(base, page):
            assert page == 1
            return base
        @staticmethod
        def fetch_page(url):
            return [dict(url='https://missav.ai/abc-123', title='test')]
    monkeypatch.setitem(browse_service.BROWSERS, 'MissAV', Browser)
    result = browse_service.fetch_listing('MissAV', language='en')
    assert result['videos'][0]['_source_listing_url'] == result['base']
    assert result['videos'][0]['_site'] == 'MissAV'


def test_advanced_filter_pagination_keeps_all_selected_values():
    from urllib.parse import parse_qs, urlsplit
    base = browse_service.hanime_filter_url(dict(query='a & b', genre='裏番', sort='觀看次數',
        date='過去 1 週', duration='10 分鐘 +', tags=['中文字幕', '無碼']))
    parsed = parse_qs(urlsplit(browse_service.listing_url('Hanime1', base, '', 3)).query)
    assert parsed == dict(query=['a & b'], genre=['裏番'], sort=['觀看次數'], date=['過去 1 週'],
                         duration=['10 分鐘 +'], **{'tags[]': ['中文字幕', '無碼'], 'page': ['3']})
    url = browse_service.listing_url('JableTV', 'https://jable.tv/search/?q=a%20b&from=7', '', 2)
    assert parse_qs(urlsplit(url).query) == dict(q=['a b'], **{'from': ['2']})
