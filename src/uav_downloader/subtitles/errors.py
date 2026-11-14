"""Localized subtitle errors shared by GUI and headless task consumers."""
from uav_downloader.i18n.locales import T
from uav_downloader.subtitles.settings import get_translation_settings


def translation_failure_message(error, settings_getter=None):
    message = str(error or '').strip()
    try:
        uses_api = (settings_getter or get_translation_settings)().uses_api
    except Exception:
        uses_api = False
    if not uses_api:
        return message
    return f'{message} {T("translation_api_failure_local_hint")}'.strip()
