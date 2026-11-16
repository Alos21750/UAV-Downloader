import pytest

from uav_downloader.i18n import locales
from uav_downloader.ui import translation_settings as ui
from uav_downloader.subtitles.settings import (
    ANTHROPIC,
    GEMINI,
    LOCAL,
    OPENAI,
    OPENAI_COMPATIBLE,
    TranslationSettings,
)


@pytest.mark.parametrize("language", ("zh", "en", "zh-Hans", "ja", "th"))
def test_every_provider_has_a_localized_label(language):
    locales.set_lang(language)
    labels = {
        ui.provider_display_name(provider)
        for provider in (
            LOCAL, OPENAI, ANTHROPIC, GEMINI, OPENAI_COMPATIBLE)
    }
    assert len(labels) == 5
    assert all(not label.startswith("translation_") for label in labels)


def test_compatible_presets_use_complete_chat_completions_urls():
    for endpoint, model in ui._COMPATIBLE_PRESETS.values():
        assert endpoint.endswith("/chat/completions")
        assert model
    assert ui._COMPATIBLE_PRESETS["DeepSeek"][1] == "deepseek-v4-flash"


def test_provider_summary_never_contains_the_api_key(monkeypatch):
    secret = "not-for-display"
    monkeypatch.setattr(
        ui,
        "get_translation_settings",
        lambda *_args, **_kwargs: TranslationSettings(
            provider=OPENAI,
            base_url="https://api.openai.com/v1",
            model="example-model",
            api_key=secret,
            api_key_source="protected",
        ),
    )
    locales.set_lang("en")
    summary = ui.translation_provider_summary()
    assert "OpenAI" in summary
    assert secret not in summary


def test_api_failure_adds_local_fallback_hint_without_profile_details(
        monkeypatch):
    secret = "not-for-display"
    monkeypatch.setattr(
        ui,
        "get_translation_settings",
        lambda *_args, **_kwargs: TranslationSettings(
            provider=OPENAI,
            base_url="https://api.openai.com/v1",
            model="example-model",
            api_key=secret,
            api_key_source="protected",
        ),
    )
    locales.set_lang("en")
    message = ui.translation_failure_message("HTTP 429")
    assert "HTTP 429" in message
    assert "switch back to Local" in message
    assert secret not in message
