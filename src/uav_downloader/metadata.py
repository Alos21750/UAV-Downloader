"""Single source of truth for public product and release identity."""

from __future__ import annotations


PRODUCT_NAME = "UAV Downloader"
VERSION = "3.1.3"
COMPANY_NAME = "ALOS (Alos21750)"
GITHUB_REPOSITORY = "Alos21750/UAV-Downloader"
LEGACY_GITHUB_REPOSITORIES = (
    "Alos21750/ALOS-Unified-AV-Downloader",
    "Alos21750/JableTV-MissAV-Downloader-GUI-2026",
)

APP_DISPLAY_NAMES = {
    "browse": "UAV Browser",
    "watch": "UAV Watcher",
    "headless": "UAV Downloader CLI",
}

APP_DESCRIPTIONS = {
    "browse": "Interactive multi-site AV/JAV downloader",
    "watch": "Unattended scheduled auto-downloader",
    "headless": "Docker and command-line downloader",
}

_RELEASE_ASSETS = {
    "browse": (
        "UAV_Browser.exe",
        "ALOS_Browse.exe",
        "JableTV_Modern.exe",
    ),
    "watch": (
        "UAV_Watcher.exe",
        "ALOS_Watch.exe",
        "Jable_smalltool.exe",
    ),
    "watch-portable": (
        "UAV_Watcher_portable.zip",
        "ALOS_Watch_portable.zip",
        "Jable_smalltool_portable.zip",
    ),
    "asr-pack": (
        "UAV_reazonspeech_asr_v1.zip",
        "ALOS_reazonspeech_asr_v1.zip",
        "Jable_reazonspeech_asr_v1.zip",
    ),
}

FEATURED_SITES = ("JableTV", "MissAV", "SupJav", "Hanime1")


def release_asset_candidates(channel: str) -> tuple[str, ...]:
    """Return canonical-first asset names for a logical release channel."""
    try:
        return _RELEASE_ASSETS[str(channel)]
    except KeyError as exc:
        raise ValueError(f"unknown release channel: {channel!r}") from exc


def github_api_latest_url() -> str:
    return (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}/releases/latest"
    )
