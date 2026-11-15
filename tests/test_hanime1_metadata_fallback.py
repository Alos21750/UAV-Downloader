"""Independent regression checks for PR #66's optional metadata fallback."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from requests.exceptions import Timeout

from uav_downloader.sites import hanime1


@pytest.mark.parametrize('has_source', [True, False])
@pytest.mark.parametrize('failure', [Timeout, hanime1.MirrorsBlockedError])
def test_optional_title_failure_only_tolerated_with_playable_source(monkeypatch, has_source, failure):
    def request(_session, url):
        if url == hanime1.HANIME1_HOME:
            return SimpleNamespace(status_code=200)
        if '/watch?' in url:
            source = '<source src="https://cdn.example.test/42.mp4">' if has_source else ''
            return SimpleNamespace(status_code=200, content=source.encode())
        raise failure('controlled metadata failure')

    monkeypatch.setattr(hanime1, '_make_scraper', nullcontext)
    monkeypatch.setattr(hanime1, '_request', request)
    crawler = hanime1.SiteHanime1.__new__(hanime1.SiteHanime1)
    crawler._url = 'https://hanime1.me/watch?v=42'
    crawler._resolution_pref = 'highest'
    if not has_source:
        with pytest.raises(failure, match='controlled metadata failure'):
            crawler.get_url_infos()
    else:
        crawler.get_url_infos()
        assert crawler._targetName == 'Hanime1-42'
        assert crawler._direct_url == 'https://cdn.example.test/42.mp4'
