import json
import os

import pytest

from uav_downloader.subtitles import settings


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    path = tmp_path / "translation_api.json"
    monkeypatch.setattr(settings, "_settings_path", lambda: str(path))
    for name in (
        "UAV_TRANSLATION_API_KEY",
        "ALOS_TRANSLATION_API_KEY",
        "JABLE_TRANSLATION_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return path


def test_default_is_fully_local_and_creates_no_file(isolated_settings):
    loaded = settings.get_translation_settings()

    assert loaded.provider == settings.LOCAL
    assert not loaded.uses_api
    assert loaded.api_key == ""
    assert not isolated_settings.exists()


def test_openai_default_model_is_a_supported_model_name():
    assert settings.provider_defaults(settings.OPENAI)['model'] == 'gpt-6-luna'


def test_provider_and_profile_normalization(isolated_settings):
    assert settings.normalize_provider("OPENAI_COMPATIBLE") == (
        settings.OPENAI_COMPATIBLE)
    assert settings.normalize_provider("unknown") == settings.LOCAL
    loaded = settings.save_translation_profile(
        settings.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1/",
        model="qwen2.5:7b",
    )

    assert loaded.provider == settings.OPENAI_COMPATIBLE
    assert loaded.base_url == "http://127.0.0.1:11434/v1"
    assert loaded.model == "qwen2.5:7b"
    assert loaded.credential_available


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_api_key_round_trip_is_dpapi_protected_not_plaintext(
        isolated_settings):
    secret = "sk-test-secret-that-must-not-be-written"
    loaded = settings.save_translation_profile(
        settings.OPENAI,
        base_url="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        api_key=secret,
    )

    raw_text = isolated_settings.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    assert secret not in raw_text
    assert raw["profiles"]["openai"]["api_key_protected"].startswith("dpapi:")
    assert loaded.api_key == secret
    assert loaded.api_key_source == "protected"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_provider_profiles_and_keys_are_independent(isolated_settings):
    settings.save_translation_profile(
        settings.OPENAI,
        base_url="https://api.openai.com/v1",
        model="openai-model",
        api_key="openai-secret",
    )
    settings.save_translation_profile(
        settings.ANTHROPIC,
        base_url="https://api.anthropic.com/v1",
        model="anthropic-model",
        api_key="anthropic-secret",
    )

    assert settings.get_translation_settings(settings.OPENAI).api_key == (
        "openai-secret")
    assert settings.get_translation_settings(settings.ANTHROPIC).api_key == (
        "anthropic-secret")
    assert settings.get_translation_settings().provider == settings.ANTHROPIC


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_blank_key_preserves_saved_secret_and_explicit_clear_removes_it(
        isolated_settings):
    settings.save_translation_profile(
        settings.GEMINI,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-test",
        api_key="gemini-secret",
    )
    settings.save_translation_profile(
        settings.GEMINI,
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-test-2",
        api_key="",
    )
    assert settings.get_translation_settings().api_key == "gemini-secret"

    settings.clear_translation_api_key()
    assert settings.get_translation_settings().api_key == ""
    raw = json.loads(isolated_settings.read_text(encoding="utf-8"))
    assert "api_key_protected" not in raw["profiles"]["gemini"]


def test_environment_key_overrides_protected_or_missing_key(
        isolated_settings, monkeypatch):
    settings.save_translation_profile(
        settings.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model="custom-model",
    )
    monkeypatch.setenv("UAV_TRANSLATION_API_KEY", "environment-secret")

    loaded = settings.get_translation_settings()

    assert loaded.api_key == "environment-secret"
    assert loaded.api_key_source == "environment"
    assert "environment-secret" not in isolated_settings.read_text(
        encoding="utf-8")


@pytest.mark.parametrize(
    "name",
    ("ALOS_TRANSLATION_API_KEY", "JABLE_TRANSLATION_API_KEY"),
)
def test_legacy_environment_key_aliases_remain_supported(
        isolated_settings, monkeypatch, name):
    settings.save_translation_profile(
        settings.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model="custom-model",
    )
    monkeypatch.setenv(name, "legacy-environment-secret")

    loaded = settings.get_translation_settings()

    assert loaded.api_key == "legacy-environment-secret"
    assert loaded.api_key_source == "environment"


def test_settings_repr_and_comparison_never_expose_or_depend_on_secret():
    first = settings.TranslationSettings(
        provider=settings.OPENAI,
        base_url="https://api.openai.com/v1",
        model="model",
        api_key="first-secret",
    )
    second = settings.TranslationSettings(
        provider=settings.OPENAI,
        base_url="https://api.openai.com/v1",
        model="model",
        api_key="second-secret",
    )

    assert "first-secret" not in repr(first)
    assert first == second
    assert not hasattr(first, "runtime_dict")


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        (settings.OPENAI, "https://attacker.invalid/v1"),
        (settings.ANTHROPIC, "https://attacker.invalid/v1"),
        (settings.GEMINI, "https://attacker.invalid/v1beta"),
    ],
)
def test_official_provider_profiles_reject_custom_hosts(
        isolated_settings, provider, url):
    with pytest.raises(
            settings.TranslationSettingsError,
            match="official API URL"):
        settings.save_translation_profile(
            provider, base_url=url, model="model", api_key="secret")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "ftp://example.com/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/v1#fragment",
    ],
)
def test_unsafe_api_urls_are_rejected(isolated_settings, url):
    with pytest.raises(settings.TranslationSettingsError):
        settings.save_translation_profile(
            settings.OPENAI_COMPATIBLE,
            base_url=url,
            model="model",
        )


def test_corrupt_or_plaintext_secret_is_never_returned(isolated_settings):
    isolated_settings.write_text(
        json.dumps(
            {
                "selected_provider": "openai",
                "profiles": {
                    "openai": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "model",
                        "api_key": "legacy-plaintext-must-be-ignored",
                        "api_key_protected": "dpapi:not-valid-base64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.get_translation_settings()

    assert loaded.api_key == ""
    assert loaded.api_key_source == "none"


def test_any_settings_write_purges_plaintext_and_unknown_profile_fields(
        isolated_settings):
    isolated_settings.write_text(
        json.dumps(
            {
                "selected_provider": "openai",
                "untrusted_top_level": "remove-me",
                "profiles": {
                    "openai": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "model",
                        "api_key": "legacy-plaintext-must-be-purged",
                        "unknown_secret": "remove-me-too",
                    },
                    "unknown-provider": {
                        "api_key": "unknown-provider-secret",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    settings.select_translation_provider(settings.LOCAL)

    text = isolated_settings.read_text(encoding="utf-8")
    raw = json.loads(text)
    assert "legacy-plaintext-must-be-purged" not in text
    assert "unknown-provider-secret" not in text
    assert "untrusted_top_level" not in raw
    assert set(raw) == {"schema", "selected_provider", "profiles"}
    assert set(raw["profiles"]["openai"]) == {"base_url", "model"}


def test_hand_edited_official_host_falls_back_to_the_official_url(
        isolated_settings):
    isolated_settings.write_text(
        json.dumps(
            {
                "selected_provider": "openai",
                "profiles": {
                    "openai": {
                        "base_url": "https://attacker.invalid/v1",
                        "model": "model",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.get_translation_settings()

    assert loaded.provider == settings.OPENAI
    assert loaded.base_url == "https://api.openai.com/v1"


def test_local_provider_never_loads_generic_environment_credential(
        isolated_settings, monkeypatch):
    monkeypatch.setenv(
        "UAV_TRANSLATION_API_KEY",
        "must-not-be-loaded-while-local")

    loaded = settings.get_translation_settings()

    assert loaded.provider == settings.LOCAL
    assert loaded.api_key == ""
    assert loaded.api_key_source == "none"


def test_switching_back_to_local_preserves_optional_profile(
        isolated_settings):
    settings.save_translation_profile(
        settings.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model="custom-model",
    )
    settings.select_translation_provider(settings.LOCAL)

    assert settings.get_translation_settings().provider == settings.LOCAL
    profile = settings.get_translation_settings(
        settings.OPENAI_COMPATIBLE)
    assert profile.base_url == "https://example.test/v1"
    assert profile.model == "custom-model"
