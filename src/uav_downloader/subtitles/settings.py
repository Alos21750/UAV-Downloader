"""Shared, DPAPI-protected settings for optional LLM subtitle translation.

The default provider is always the bundled local translator.  API credentials
are never stored in plaintext: Windows builds use user-scoped DPAPI, while
non-Windows source runs may provide credentials through environment variables.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from uav_downloader.core.paths import product_data_dir


LOCAL = "local"
OPENAI = "openai"
ANTHROPIC = "anthropic"
GEMINI = "gemini"
OPENAI_COMPATIBLE = "openai-compatible"
PROVIDERS = (
    LOCAL,
    OPENAI,
    ANTHROPIC,
    GEMINI,
    OPENAI_COMPATIBLE,
)

# Models remain editable in the UI.  These are conservative starting values,
# not a claim that a provider's newest model will always keep this name.
PROVIDER_DEFAULTS = {
    LOCAL: {"base_url": "", "model": ""},
    OPENAI: {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-6-luna",
    },
    ANTHROPIC: {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-haiku-4-5-20251001",
    },
    GEMINI: {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-3.5-flash-lite",
    },
    OPENAI_COMPATIBLE: {"base_url": "", "model": ""},
}

_SCHEMA = 1
_MAX_SECRET_LENGTH = 4096
_MAX_MODEL_LENGTH = 160
_MAX_URL_LENGTH = 2048
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Keep the original entropy bytes forever: changing them would make existing
# DPAPI-protected API keys undecryptable after the UAV rename.
_DPAPI_ENTROPY = b"JableTV.translation-api.v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_LOCK = threading.RLock()


class TranslationSettingsError(ValueError):
    """A safe, user-facing translation-setting validation error."""


@dataclass(frozen=True)
class TranslationSettings:
    provider: str = LOCAL
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False, compare=False)
    api_key_source: str = "none"

    @property
    def uses_api(self) -> bool:
        return self.provider != LOCAL

    @property
    def credential_available(self) -> bool:
        return bool(self.api_key) or self.provider == OPENAI_COMPATIBLE

def _settings_path() -> str:
    return str(product_data_dir() / "translation_api.json")


def normalize_provider(value: object) -> str:
    provider = str(value or "").strip().lower().replace("_", "-")
    return provider if provider in PROVIDERS else LOCAL


def _normalize_model(value: object, provider: str) -> str:
    model = str(value or "").strip()
    if not model:
        model = PROVIDER_DEFAULTS[provider]["model"]
    if len(model) > _MAX_MODEL_LENGTH or _CONTROL_RE.search(model):
        raise TranslationSettingsError("Invalid translation model name")
    if provider != LOCAL and not model:
        raise TranslationSettingsError("A translation model name is required")
    return model


def _normalize_base_url(value: object, provider: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = PROVIDER_DEFAULTS[provider]["base_url"]
    if provider == LOCAL:
        return ""
    if not raw or len(raw) > _MAX_URL_LENGTH or _CONTROL_RE.search(raw):
        raise TranslationSettingsError("A valid translation API URL is required")
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TranslationSettingsError(
            "Translation API URLs cannot contain credentials, query, or fragment")
    if parsed.scheme == "http":
        if host not in _LOOPBACK_HOSTS:
            raise TranslationSettingsError(
                "Translation API URLs must use HTTPS except on this computer")
    elif parsed.scheme != "https":
        raise TranslationSettingsError(
            "Translation API URLs must use HTTPS except on this computer")
    if not host:
        raise TranslationSettingsError("A valid translation API URL is required")
    path = parsed.path.rstrip("/")
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    if (
            provider in {OPENAI, ANTHROPIC, GEMINI}
            and normalized != PROVIDER_DEFAULTS[provider]["base_url"]):
        raise TranslationSettingsError(
            "Official providers must use their official API URL")
    return normalized


def _read_raw() -> dict:
    try:
        with open(_settings_path(), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _sanitized_profiles(value: object) -> dict:
    profiles = value if isinstance(value, dict) else {}
    sanitized = {}
    for provider in PROVIDERS:
        if provider == LOCAL:
            continue
        candidate = profiles.get(provider)
        if not isinstance(candidate, dict):
            continue
        profile = {}
        for name in ("base_url", "model"):
            field_value = candidate.get(name)
            if isinstance(field_value, str):
                profile[name] = field_value
        protected = candidate.get("api_key_protected")
        if isinstance(protected, str) and protected.startswith("dpapi:"):
            profile["api_key_protected"] = protected
        sanitized[provider] = profile
    return sanitized


@contextmanager
def _interprocess_settings_lock(timeout_seconds: float = 15.0):
    """Serialize profile updates made by UAV Browser and UAV Watcher."""
    path = _settings_path() + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+b")
    acquired = False
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(
                        handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TranslationSettingsError(
                        "Translation settings are busy; try again")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _write_raw(raw: dict) -> None:
    path = _settings_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    safe_raw = {
        "schema": _SCHEMA,
        "selected_provider": normalize_provider(raw.get("selected_provider")),
        "profiles": _sanitized_profiles(raw.get("profiles")),
    }
    descriptor, temp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        handle = os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with handle:
            json.dump(
                safe_raw, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        temp = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp:
            try:
                os.remove(temp)
            except FileNotFoundError:
                pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(data: bytes) -> tuple[_DataBlob, object]:
    size = len(data)
    buffer = (ctypes.c_ubyte * max(1, size))()
    if size:
        ctypes.memmove(buffer, data, size)
    return (
        _DataBlob(size, ctypes.cast(
            buffer, ctypes.POINTER(ctypes.c_ubyte))),
        buffer,
    )


def _protect_secret(secret: str) -> str:
    if os.name != "nt":
        raise TranslationSettingsError(
            "Persistent API keys require Windows credential protection")
    encoded = str(secret).encode("utf-8")
    if not encoded or len(encoded) > _MAX_SECRET_LENGTH:
        raise TranslationSettingsError("Invalid translation API key")
    source, source_buffer = _input_blob(encoded)
    entropy, entropy_buffer = _input_blob(_DPAPI_ENTROPY)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_bool
    if not crypt32.CryptProtectData(
            ctypes.byref(source), "UAV Downloader translation API key",
            ctypes.byref(entropy), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output)):
        raise TranslationSettingsError("Windows could not protect the API key")
    try:
        protected = ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, entropy_buffer
    return "dpapi:" + base64.b64encode(protected).decode("ascii")


def _unprotect_secret(value: object) -> str:
    protected = str(value or "")
    if os.name != "nt" or not protected.startswith("dpapi:"):
        return ""
    try:
        encrypted = base64.b64decode(
            protected.removeprefix("dpapi:"), validate=True)
    except (ValueError, UnicodeError):
        return ""
    source, source_buffer = _input_blob(encrypted)
    entropy, entropy_buffer = _input_blob(_DPAPI_ENTROPY)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_bool
    if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output)):
        return ""
    try:
        plaintext = ctypes.string_at(output.pbData, output.cbData)
        if not plaintext or len(plaintext) > _MAX_SECRET_LENGTH:
            return ""
        decoded = plaintext.decode("utf-8")
        return "" if _CONTROL_RE.search(decoded) else decoded
    except UnicodeError:
        return ""
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, entropy_buffer


def _environment_key(provider: str) -> str:
    if provider == LOCAL:
        return ""
    # UAV is canonical. The older prefixes stay readable so existing
    # unattended deployments do not lose their configured credential.
    names = [
        "UAV_TRANSLATION_API_KEY",
        "ALOS_TRANSLATION_API_KEY",
        "JABLE_TRANSLATION_API_KEY",
    ]
    if provider == OPENAI:
        names.append("OPENAI_API_KEY")
    elif provider == ANTHROPIC:
        names.append("ANTHROPIC_API_KEY")
    elif provider == GEMINI:
        names.extend(("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    for name in names:
        value = os.environ.get(name, "").strip()
        if (
                value
                and not _CONTROL_RE.search(value)
                and len(value.encode("utf-8")) <= _MAX_SECRET_LENGTH):
            return value
    return ""


def get_translation_settings(
        provider: object = None) -> TranslationSettings:
    with _LOCK:
        raw = _read_raw()
    selected = normalize_provider(
        provider if provider is not None else raw.get("selected_provider"))
    defaults = PROVIDER_DEFAULTS[selected]
    profiles = raw.get("profiles")
    profile = (
        profiles.get(selected, {})
        if isinstance(profiles, dict)
        and isinstance(profiles.get(selected), dict)
        else {}
    )
    try:
        base_url = _normalize_base_url(
            profile.get("base_url", defaults["base_url"]), selected)
        model = _normalize_model(
            profile.get("model", defaults["model"]), selected)
    except TranslationSettingsError:
        base_url = defaults["base_url"]
        model = defaults["model"]
    environment = _environment_key(selected)
    if environment:
        api_key, source = environment, "environment"
    else:
        api_key = _unprotect_secret(profile.get("api_key_protected"))
        source = "protected" if api_key else "none"
    return TranslationSettings(
        provider=selected,
        base_url=base_url,
        model=model,
        api_key=api_key,
        api_key_source=source,
    )


def select_translation_provider(provider: object) -> TranslationSettings:
    selected = normalize_provider(provider)
    with _LOCK:
        with _interprocess_settings_lock():
            raw = _read_raw()
            raw = {
                "schema": _SCHEMA,
                "selected_provider": selected,
                "profiles": _sanitized_profiles(raw.get("profiles")),
            }
            _write_raw(raw)
    return get_translation_settings()


def save_translation_profile(
        provider: object,
        *,
        base_url: object = "",
        model: object = "",
        api_key: object = None,
        clear_key: bool = False,
        select: bool = True,
) -> TranslationSettings:
    selected = normalize_provider(provider)
    if selected == LOCAL:
        return select_translation_provider(LOCAL)
    normalized_url = _normalize_base_url(base_url, selected)
    normalized_model = _normalize_model(model, selected)
    with _LOCK:
        with _interprocess_settings_lock():
            raw = _read_raw()
            profiles = _sanitized_profiles(raw.get("profiles"))
            existing = (
                dict(profiles.get(selected))
                if isinstance(profiles.get(selected), dict)
                else {}
            )
            profile = {
                "base_url": normalized_url,
                "model": normalized_model,
            }
            protected = existing.get("api_key_protected")
            if clear_key:
                protected = None
            elif api_key is not None and str(api_key).strip():
                candidate = str(api_key).strip()
                if _CONTROL_RE.search(candidate):
                    raise TranslationSettingsError(
                        "Invalid translation API key")
                protected = _protect_secret(candidate)
            if isinstance(protected, str) and protected.startswith("dpapi:"):
                profile["api_key_protected"] = protected
            profiles[selected] = profile
            raw = {
                "schema": _SCHEMA,
                "selected_provider": (
                    selected if select
                    else normalize_provider(raw.get("selected_provider"))
                ),
                "profiles": profiles,
            }
            _write_raw(raw)
    return get_translation_settings(selected)


def clear_translation_api_key(provider: object = None) -> TranslationSettings:
    selected = normalize_provider(
        provider if provider is not None
        else get_translation_settings().provider)
    settings = get_translation_settings(selected)
    if selected == LOCAL:
        return settings
    return save_translation_profile(
        selected,
        base_url=settings.base_url,
        model=settings.model,
        clear_key=True,
        select=(get_translation_settings().provider == selected),
    )


def provider_defaults(provider: object) -> dict[str, str]:
    selected = normalize_provider(provider)
    return dict(PROVIDER_DEFAULTS[selected])
