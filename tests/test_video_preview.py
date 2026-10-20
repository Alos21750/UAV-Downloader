"""Preview must open only a known source page, without enqueueing a download."""
import pytest

from uav_downloader.ui.preview import open_video_preview


@pytest.mark.parametrize('url', [
    'https://jable.tv/videos/ssni-123/',
    'https://supjav.com/462685.html',
    'https://missav.ai/en/sone-543',
    'https://hanime1.me/watch?v=12345',
])
def test_preview_opens_supported_source_in_browser(url):
    calls = []
    def opener(value, **kwargs):
        calls.append((value, kwargs))
        return True
    assert open_video_preview(url, opener=opener)
    assert calls == [(url, {'new': 2})]


@pytest.mark.parametrize('url', [
    'file:///C:/private.txt', 'javascript:alert(1)',
    'https://missav.ai.evil.example/en/sone-543',
    'https://user:password@missav.ai/en/sone-543',
    'https://127.0.0.1/private', 'https://missav.ai/new',
    'https://jable.tv/videos/../../escape/',
    'https://jable.tv/videos/%2e%2e%2fescape/',
    'https://missav.ai/en/sone-543\n', 'https://missav.ai/' + '1' * 5000,
])
def test_preview_rejects_untrusted_url_without_opening(url):
    calls = []
    with pytest.raises(ValueError):
        open_video_preview(url, opener=lambda *a, **k: calls.append(a))
    assert not calls


def test_preview_reports_browser_launch_failure():
    assert not open_video_preview(
        'https://jable.tv/videos/ssni-123/', opener=lambda *a, **k: False)


def test_card_preview_never_changes_download_queue(monkeypatch):
    from types import SimpleNamespace
    from uav_downloader.apps import browse
    calls, messages = [], []
    monkeypatch.setattr(browse, 'open_video_preview',
                        lambda url: calls.append(url) or True, raising=False)
    app = SimpleNamespace(_status_lbl=SimpleNamespace(
        configure=lambda **kw: messages.append(kw['text'])))
    browse.ModernApp._preview_video(app, 'https://jable.tv/videos/ssni-123/')
    assert calls == ['https://jable.tv/videos/ssni-123/']
    assert messages
