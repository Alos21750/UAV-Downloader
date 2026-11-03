"""Offline regressions for #59: failed pages must not become empty results."""

from types import SimpleNamespace

import pytest

from uav_downloader.core import config
from uav_downloader.sites import base, supjav


LISTING = b'<div class="post"><a href="https://supjav.com/123.html" title="Fixture">Fixture</a></div>'


class Scraper:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(kwargs)
        status, content = next(self.pages)
        return SimpleNamespace(url=url, status_code=status, content=content, headers={})


@pytest.fixture
def browser_env(monkeypatch):
    monkeypatch.setattr(config, 'MIRRORS', {'supjav': ['supjav.com']})
    monkeypatch.setattr(config, 'RATE_LIMIT_WAITS', {})
    monkeypatch.setattr(config, 'get_cf_override', lambda host: None)
    monkeypatch.setattr(config, 'proxy_request_kwargs', lambda: {})
    monkeypatch.setattr(base, '_active_host', {})

    def install(*pages):
        scraper = Scraper(pages)
        monkeypatch.setattr(supjav.SupJavBrowser, '_get_scraper', lambda: scraper)
        return scraper

    return install


def test_unrecognized_override_response_retries_plain_request(browser_env, monkeypatch):
    monkeypatch.setattr(config, 'get_cf_override', lambda host: {'cookie': 'fixture', 'ua': 'Fixture/1'})
    scraper = browser_env((200, b'<html><body>Temporary gateway response</body></html>'), (200, LISTING))
    videos = supjav.SupJavBrowser.fetch_page('https://supjav.com/')
    assert [video['url'] for video in videos] == ['https://supjav.com/123.html']
    assert len(scraper.calls) == 2
    assert scraper.calls[0]['cookies'] == {'cf_clearance': 'fixture'}
    assert scraper.calls[1]['cookies'] is None


@pytest.mark.parametrize('content', [b'', b'<html><body>Upstream unavailable</body></html>', b'<html><title>SupJav</title></html>'])
def test_unknown_page_raises_instead_of_returning_empty(browser_env, content):
    browser_env((200, content))
    with pytest.raises(RuntimeError, match='SupJav'):
        supjav.SupJavBrowser.fetch_page('https://supjav.com/?s=appt')


def test_clear_challenge_remains_blocked(browser_env):
    browser_env((403, b'<title>Just a moment...</title>'))
    with pytest.raises(base.MirrorsBlockedError):
        supjav.SupJavBrowser.fetch_page('https://supjav.com/')


@pytest.mark.parametrize('content', [
    b'<html><body class="search search-no-results"><main><p>Nothing Found</p></main></body></html>',
    b'<html><body><section class="no-results not-found"><p>Nothing Found</p></section></body></html>',
])
def test_explicit_search_no_results_is_empty(browser_env, content):
    browser_env((200, content))
    assert supjav.SupJavBrowser.fetch_page('https://supjav.com/?s=fixture') == []


def test_successful_listing_with_cloudflare_script_still_works(browser_env):
    browser_env((200, LISTING + b'<script src="/cdn-cgi/challenge-platform/script.js"></script>'))
    assert len(supjav.SupJavBrowser.fetch_page('https://supjav.com/')) == 1


def test_http_error_with_listing_like_content_is_not_success(browser_env):
    browser_env((500, LISTING))
    with pytest.raises(RuntimeError, match='SupJav'):
        supjav.SupJavBrowser.fetch_page('https://supjav.com/')


def test_parser_exception_is_not_reported_as_empty_search(browser_env, monkeypatch):
    browser_env((200, LISTING))

    def fail(soup):
        raise ValueError('fixture parser error')

    monkeypatch.setattr(supjav, '_parse_videos', fail)
    with pytest.raises(ValueError, match='fixture parser error'):
        supjav.SupJavBrowser.fetch_page('https://supjav.com/')


def test_browser_helper_receives_visible_error(browser_env):
    from uav_downloader.apps.browse import fetch_page_data

    browser_env((200, b'<html><body>Gateway response</body></html>'))
    result = fetch_page_data(supjav.SupJavBrowser, 'https://supjav.com/?s=appt')
    assert result['videos'] == []
    assert 'SupJav' in result['error']


def test_mirror_validator_error_does_not_skip_plain_fallback(browser_env, monkeypatch):
    monkeypatch.setattr(config, 'get_cf_override', lambda host: {'cookie': 'fixture', 'ua': 'Fixture/1'})
    scraper = browser_env((200, b'bad'), (200, LISTING))

    def validate(response):
        if response.content == b'bad':
            raise ValueError('fixture parse failure')
        return True

    response, host, reason = base.fetch_with_mirrors(
        scraper, 'https://supjav.com/', 'supjav', validate)
    assert (host, reason) == ('supjav.com', 'ok')
    assert response.content == LISTING
    assert len(scraper.calls) == 2
