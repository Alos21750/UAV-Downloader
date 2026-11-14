"""Reactive QML translations without rebuilding windows or list delegates."""
from PySide6.QtCore import QObject, Property, Signal, Slot

from uav_downloader.i18n.locales import FONTS, STATE_LABELS, STRINGS, get_lang, set_lang, state_label
from uav_downloader.i18n.qt_strings import TEXT, localized_text
STOPPING_STATES = frozenset()  # Upstream queue has no transitional stop states.
from uav_downloader.i18n import sites as site_i18n
from uav_downloader.ui import theme


class I18nController(QObject):
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._language = None
        self._strings, self._states, self._options = {}, {}, {}
        settings.valuesChanged.connect(self.reload)
        self.reload()

    @Slot()
    def reload(self):
        language = self.settings.values.get('lang', get_lang())
        if language not in STRINGS:
            language = 'en'
        if language == self._language:
            return
        self._language = language
        set_lang(language)
        self._strings = {**STRINGS['en'], **STRINGS[language]}
        self._strings.update({key: localized_text(key, language) for key in TEXT})
        self._states = {code: state_label(code) for code in STATE_LABELS}
        options = {
            'highest': 'resolution_highest', 'lowest': 'resolution_lowest',
            'full-title': 'filename_mode_full', 'code-only': 'filename_mode_code',
            'auto': 'recognition_quality_auto', 'quality': 'recognition_quality_quality',
            'balanced': 'recognition_quality_balanced', 'fast': 'recognition_quality_fast',
            'none': 'subtitle_none', 'ja': 'subtitle_ja', 'en': 'subtitle_en', 'zh': 'subtitle_zh', 'all': 'subtitle_all',
            'system': 'qt_system', 'light': 'qt_light', 'dark': 'qt_dark', 'direct': 'qt_direct', 'manual': 'qt_manual',
            'local': 'translation_provider_local', 'openai': 'translation_provider_openai',
            'anthropic': 'translation_provider_anthropic', 'gemini': 'translation_provider_gemini',
            'openai-compatible': 'translation_provider_compatible',
            'interval': 'st_schedule_interval', 'daily': 'st_schedule_daily',
            'chinese-subtitle': 'st_pref_chinese', 'uncensored': 'st_pref_uncensored',
            'standard': 'st_pref_standard', 'english-subtitle': 'st_pref_english', 'reducing-mosaic': 'st_pref_reducing_mosaic',
        }
        self._options = {code: self._strings[key] for code, key in options.items()}
        self.changed.emit()

    @Property(str, notify=changed)
    def language(self): return self._language

    @Property(str, notify=changed)
    def fontFamily(self): return FONTS[self._language]

    @Property('QVariantMap', constant=True)
    def desktopColors(self):
        return {name: list(value) for name, value in vars(theme).items()
                if name.isupper() and isinstance(value, tuple)}

    @Property('QVariantMap', notify=changed)
    def strings(self): return self._strings

    @Property('QVariantMap', notify=changed)
    def states(self): return self._states

    @Property('QStringList', constant=True)
    def stoppingStates(self): return sorted(STOPPING_STATES)

    @Property('QVariantMap', notify=changed)
    def options(self): return self._options

    @Property('QVariantMap', notify=changed)
    def tagGroups(self):
        return {key: site_i18n.loc(site_i18n.TAG_GROUPS, key, key) for key in site_i18n.TAG_GROUPS}
