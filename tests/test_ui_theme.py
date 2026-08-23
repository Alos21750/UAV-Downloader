import inspect
import re
import types
from pathlib import Path

from uav_downloader.apps import browse as gui_modern
from uav_downloader.apps import watch as jable_smalltool
from uav_downloader.i18n import locales
from uav_downloader.ui import theme as ui_theme


def _rgb(hex_color):
    return tuple(int(hex_color[index:index + 2], 16) / 255
                 for index in (1, 3, 5))


def _luminance(hex_color):
    channels = []
    for value in _rgb(hex_color):
        channels.append(value / 12.92 if value <= 0.04045
                        else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a, b):
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_responsive_breakpoints_prioritize_readability():
    assert ui_theme.browse_columns_for_width(979) == 2
    assert ui_theme.browse_columns_for_width(1080) == 3
    assert ui_theme.browse_columns_for_width(1499) == 3
    assert ui_theme.browse_columns_for_width(1500) == 4
    assert ui_theme.category_columns_for_width(1119) == 2
    assert ui_theme.category_columns_for_width(1120) == 3


def test_shared_palette_is_valid_and_used_by_both_apps():
    tokens = (
        ui_theme.ACCENT, ui_theme.BG_DARK, ui_theme.BG_CARD,
        ui_theme.TEXT_PRI, ui_theme.TEXT_SEC, ui_theme.BORDER,
    )
    assert all(len(token) == 2 for token in tokens)
    assert all(re.fullmatch(r'#[0-9A-Fa-f]{6}', color)
               for token in tokens for color in token)
    assert gui_modern.ACCENT is ui_theme.ACCENT
    assert jable_smalltool.ACCENT is ui_theme.ACCENT


def test_primary_text_contrast_is_accessible_in_both_themes():
    for index in (0, 1):
        assert _contrast(ui_theme.TEXT_PRI[index], ui_theme.BG_DARK[index]) >= 7
        assert _contrast(ui_theme.TEXT_PRI[index], ui_theme.BG_CARD[index]) >= 7


def test_current_version_and_global_smalltool_copy_are_complete():
    assert gui_modern.APP_VERSION == jable_smalltool.APP_VERSION == '3.1.2'
    required = {
        'st_activity', 'st_progress_idle', 'st_footer_short',
        'st_categories_expand', 'st_categories_collapse',
        'st_scanning', 'st_downloading', 'st_scan_progress',
        'st_candidates_found',
        'st_calendar', 'st_date_quick', 'st_date_month_1',
        'st_date_month_2', 'st_folder_error',
        'st_version_preference', 'st_pref_chinese',
        'st_pref_uncensored', 'st_pref_standard',
        'st_pref_english', 'st_pref_reducing_mosaic',
        'st_settings_expand', 'st_settings_collapse',
        'st_activity_show', 'st_activity_hide',
        'st_schedule', 'st_schedule_title', 'st_schedule_interval',
        'st_schedule_hours', 'st_schedule_daily',
        'st_schedule_local_time', 'st_schedule_hint',
        'st_schedule_save', 'st_schedule_summary_interval',
        'st_schedule_summary_daily', 'st_schedule_invalid_hours',
        'st_schedule_invalid_time', 'st_schedule_saved',
        'st_scan_queued', 'st_waiting_schedule', 'st_stopping',
        'retry_short', 'requeue_short',
        'filename_mode_setting', 'filename_mode_full',
        'filename_mode_code', 'filename_mode_desc',
    }
    for language, strings in locales.STRINGS.items():
        assert strings['version_label'] == 'v3.1.2', language
        assert required <= strings.keys(), language


def test_windows_version_resources_match_app_version():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github' / 'workflows' / 'windows-build.yml').read_text(
        encoding='utf-8')
    assert '$expected = "3.1.2.0"' in workflow
    packaging = root / 'packaging' / 'windows'
    generator = (packaging / 'gen_version.py').read_text(
        encoding='utf-8')
    assert 'VERSION = (3, 1, 2, 0)' in generator
    for name in ('UAV_Browser.version', 'UAV_Watcher.version'):
        resource = (packaging / name).read_text(encoding='utf-8')
        assert 'filevers=(3, 1, 2, 0)' in resource
        assert "StringStruct('FileVersion', '3.1.2.0')" in resource
        assert "StringStruct('ProductName', 'UAV Downloader')" in resource
    for name in ('UAV_Browser.spec', 'UAV_Watcher.spec'):
        spec = (packaging / name).read_text(encoding='utf-8')
        assert "'numpy._core._exceptions'" in spec


def test_windows_distribution_is_hardened_and_verifiable():
    root = Path(__file__).resolve().parents[1]
    browse_spec = (
        root / 'packaging' / 'windows' / 'UAV_Browser.spec'
    ).read_text(encoding='utf-8')
    watch_spec = (
        root / 'packaging' / 'windows' / 'UAV_Watcher.spec'
    ).read_text(encoding='utf-8')
    workflow = (
        root / '.github' / 'workflows' / 'windows-build.yml'
    ).read_text(encoding='utf-8')

    for spec in (browse_spec, watch_spec):
        assert 'upx=False' in spec
        assert 'upx=True' not in spec

    # SmallTool has no update UI.  Do not bundle Modern's executable
    # downloader/self-replacement helper into its archive.
    assert "'uav_downloader.core.updater'" in browse_spec
    assert "'uav_downloader.core.updater'" not in watch_spec

    # Keep the convenient one-file build, but also ship a SmallTool onedir
    # fallback that does not self-extract through a _MEI directory.
    assert 'exclude_binaries=True' in watch_spec
    assert 'COLLECT(' in watch_spec
    assert "name='UAV_Watcher_portable'" in watch_spec
    assert "portable = '--portable' in sys.argv" in watch_spec
    assert 'UAV_Watcher.spec -- --portable' in workflow

    # The official PyInstaller guidance recommends rebuilding its bootloader
    # from source to reduce false positives tied to widely shared bootloaders.
    assert 'PYINSTALLER_COMPILE_BOOTLOADER' in workflow
    assert '--no-binary=PyInstaller' in workflow
    assert 'pip uninstall --yes PyInstaller' in workflow
    assert 'pip cache remove PyInstaller' in workflow
    # Keep the previously qualified PyInstaller release for this hotfix;
    # only the bootloader provenance changes.
    assert 'pyinstaller==6.13.0' in workflow.lower()

    # Checksums and provenance help users verify origin.  They do not replace
    # Authenticode and must remain separate from malware-detection claims.
    assert 'UAV_Watcher_portable.zip' in workflow
    assert 'UAV_SHA256SUMS.txt' in workflow
    assert '-Path "dist\\UAV_Watcher_portable"' in workflow
    assert '-Path "dist\\UAV_Watcher_portable\\*"' not in workflow
    assert 'actions/attest@v4' in workflow
    assert 'attestations: write' in workflow
    assert 'Compatibility alias is not byte-identical' not in workflow
    for documentation in (
        '"README.md"',
        '"README.en.md"',
        '"README.zh-CN.md"',
        '"README.ja.md"',
        '"THIRD_PARTY_NOTICES.md"',
        '"WINDOWS_SECURITY.md"',
    ):
        assert documentation in workflow


def test_all_readme_languages_are_complete_and_cross_linked():
    root = Path(__file__).resolve().parents[1]
    readmes = {
        'zh': root / 'README.md',
        'zh-Hans': root / 'README.zh-CN.md',
        'en': root / 'README.en.md',
        'ja': root / 'README.ja.md',
    }
    language_labels = ('繁體中文', '简体中文', 'English', '日本語')

    for language, path in readmes.items():
        assert path.is_file(), language
        text = path.read_text(encoding='utf-8')
        assert '\ufffd' not in text, language
        assert len(text.splitlines()) >= 70, language
        assert all(label in text for label in language_labels), language
        assert all(token in text for token in (
            'UAV Downloader', 'UAV_Browser.exe', 'UAV_Watcher.exe',
            '.ja.srt', '.en.srt', '.zh-TW.srt',
            'ghcr.io/alos21750/uav-downloader:latest',
            'JableTV', 'MissAV', 'SupJav', 'Hanime1',
        )), language

    assert '重新排隊' in readmes['zh'].read_text(encoding='utf-8')
    assert '重新排队' in readmes['zh-Hans'].read_text(encoding='utf-8')
    assert 'requeue' in readmes['en'].read_text(encoding='utf-8').lower()
    assert '再キュー' in readmes['ja'].read_text(encoding='utf-8')


def test_windows_security_guidance_does_not_ask_for_defender_bypass():
    root = Path(__file__).resolve().parents[1]
    traditional = (root / 'README.md').read_text(encoding='utf-8')
    english = (root / 'README.en.md').read_text(encoding='utf-8')
    security_path = root / 'WINDOWS_SECURITY.md'

    assert security_path.is_file()
    security = security_path.read_text(encoding='utf-8')
    combined = '\n'.join((traditional, english, security))

    assert 'UAV_Watcher_portable.zip' in combined
    assert 'UAV_SHA256SUMS.txt' in security
    assert 'Get-FileHash' in security
    assert 'gh attestation verify' in security
    assert (
        'gh attestation verify .\\UAV_Watcher_portable.zip'
        in security)
    assert 'https://www.microsoft.com/wdsi/filesubmission' in security
    assert 'SmartScreen' in security
    assert 'Defender Antivirus' in security
    assert '未簽章' in security
    assert 'unsigned' in security.lower()
    assert '不要為了上傳而自行還原隔離檔' in security
    assert 'Do not restore a quarantined file merely to upload it' in security
    assert '若備用包也被偵測，請停止並回報' in traditional
    assert 'stop and report it if the fallback is also detected' in english

    lowered = combined.lower()
    for unsafe_advice in (
        'disable defender',
        'turn off defender',
        'add a broad exclusion',
        '停用 defender',
        '關閉 defender',
        '整個資料夾加入排除',
    ):
        assert unsafe_advice not in lowered


def test_modern_defers_initial_workers_until_mainloop():
    init_source = inspect.getsource(gui_modern.ModernApp.__init__)
    assert 'self.after_idle(self._start_initial_background_tasks)' in init_source
    assert 'self._start_update_check(manual=False)' not in init_source

    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    calls = []
    app._is_closing = False
    app._start_update_check = lambda **kwargs: calls.append(('update', kwargs))
    app._load_categories = lambda: calls.append(('categories', {}))

    app._start_initial_background_tasks()

    assert calls == [('update', {'manual': False}), ('categories', {})]


def test_smalltool_balances_category_and_activity_regions():
    assert jable_smalltool.DEFAULT_WINDOW_WIDTH == 1180
    assert jable_smalltool.DEFAULT_WINDOW_HEIGHT == 780
    assert ui_theme.category_columns_for_width(
        jable_smalltool.DEFAULT_WINDOW_WIDTH) == 3

    source = inspect.getsource(jable_smalltool.SmallToolApp._build_ui)
    assert "main.pack(fill='both', expand=True" in source
    assert 'main.grid_columnconfigure(0, weight=1)' in source
    assert 'main.grid_rowconfigure(1, weight=1)' in source
    assert 'cfg_card.grid(row=0' in source
    assert 'selection.grid(row=1' in source
    assert 'ctrl.grid(row=2' in source
    assert 'prog_outer.grid(row=3' in source
    assert 'activity.grid(row=4' in source
    assert 'prog_outer.grid_remove()' in source
    assert 'activity.grid_remove()' in source

    collapse_source = inspect.getsource(
        jable_smalltool.SmallToolApp._set_categories_collapsed)
    assert '1, weight=0, minsize=0' in collapse_source
    assert '1, weight=1, minsize=0' in collapse_source

    start_source = inspect.getsource(
        jable_smalltool.SmallToolApp._start_worker)
    check_source = inspect.getsource(
        jable_smalltool.SmallToolApp._check_now)
    assert '_set_categories_collapsed(True)' not in start_source
    assert '_set_categories_collapsed(True)' not in check_source


def test_both_apps_expose_windows_proxy_mode_and_mode_aware_status():
    modern_ui = inspect.getsource(gui_modern.ModernApp._build_settings_tab)
    smalltool_ui = inspect.getsource(jable_smalltool.SmallToolApp._build_ui)
    for source in (modern_ui, smalltool_ui):
        assert "text=T('proxy_windows')" in source
        assert 'command=self._on_proxy_windows' in source

    for cls in (gui_modern.ModernApp, jable_smalltool.SmallToolApp):
        status_source = inspect.getsource(cls._refresh_proxy_status)
        assert "config.get_proxy_mode()" in status_source
        assert "config.refresh_system_proxy()" in status_source
        assert "T('proxy_windows_pac')" in status_source


def test_both_apps_expose_shared_recognition_quality_without_squeezing_copy():
    modern_ui = inspect.getsource(gui_modern.ModernApp._build_settings_tab)
    smalltool_ui = inspect.getsource(jable_smalltool.SmallToolApp._build_ui)

    assert 'self._recognition_quality_var = ctk.StringVar(' in modern_ui
    assert 'wraplength=SETTINGS_INLINE_HELP_WRAP' in modern_ui
    assert 'self._recognition_quality_var = tk.StringVar(' in smalltool_ui
    assert "wraplength=286" in smalltool_ui
    for source in (modern_ui, smalltool_ui):
        assert "T('recognition_quality_setting')" in source
        assert 'values=self._recognition_quality_values()' in source
        assert "T('recognition_quality_desc')" in source

    snapshot_source = inspect.getsource(
        jable_smalltool.SmallToolApp._snapshot_ui_state)
    restore_source = inspect.getsource(
        jable_smalltool.SmallToolApp._restore_ui_state)
    close_source = inspect.getsource(jable_smalltool.SmallToolApp._on_close)
    assert "'recognition_quality': config.get_recognition_quality()" in (
        snapshot_source)
    assert '_recognition_quality_label(' in restore_source
    assert "'recognition_quality'" not in close_source


def test_both_quality_selectors_persist_to_shared_config(monkeypatch):
    locales.set_lang('en')

    class _Var:
        def __init__(self):
            self.value = ''

        def set(self, value):
            self.value = value

    saved = []
    monkeypatch.setattr(
        gui_modern.config, 'set_recognition_quality',
        lambda value: saved.append(('modern', value)) or value)
    modern = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    modern._recognition_quality_var = _Var()
    assert modern._recognition_quality_values()[0] == 'Auto (recommended)'
    assert modern._recognition_quality_from_label(
        'Auto (recommended)') == 'auto'
    modern._on_recognition_quality_change(
        locales.STRINGS['en']['recognition_quality_balanced'])

    monkeypatch.setattr(
        jable_smalltool.config, 'set_recognition_quality',
        lambda value: saved.append(('smalltool', value)) or value)
    smalltool = jable_smalltool.SmallToolApp.__new__(
        jable_smalltool.SmallToolApp)
    smalltool._recognition_quality_var = _Var()
    assert smalltool._recognition_quality_values()[0] == 'Auto (recommended)'
    assert smalltool._recognition_quality_from_label(
        'Auto (recommended)') == 'auto'
    smalltool._on_recognition_quality_change(
        locales.STRINGS['en']['recognition_quality_fast'])

    assert saved == [('modern', 'balanced'), ('smalltool', 'fast')]
    assert modern._recognition_quality_var.value == 'Balanced'
    assert smalltool._recognition_quality_var.value == 'Fast'


def test_modern_concurrency_is_editable_persisted_and_clamped(monkeypatch):
    assert gui_modern.MAX_CONCURRENT == 32
    init_source = inspect.getsource(gui_modern.ModernApp.__init__)
    settings_source = inspect.getsource(
        gui_modern.ModernApp._build_settings_tab)
    footer_source = inspect.getsource(
        gui_modern.ModernApp._refresh_downloads)
    assert 'config.get_download_concurrency()' in init_source
    assert 'self._conc_entry = ctk.CTkEntry(' in settings_source
    assert "self._conc_entry.bind('<Return>'" in settings_source
    assert "'subtitle_queue_status'" in footer_source

    class _Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._conc_var = _Var('99')
    app._dlmgr = types.SimpleNamespace(max_concurrent=2)
    saved = []

    def _save(value):
        saved.append(value)
        return max(1, min(int(value), 32))

    monkeypatch.setattr(gui_modern.config, 'set_download_concurrency', _save)
    app._on_conc_change()

    assert saved == [99]
    assert app._dlmgr.max_concurrent == 32
    assert app._conc_var.get() == '32'

    app._conc_var.set('invalid')
    app._on_conc_change()
    assert saved == [99]
    assert app._conc_var.get() == '32'


def test_both_apps_expose_and_persist_per_video_worker_limit(monkeypatch):
    modern_ui = inspect.getsource(gui_modern.ModernApp._build_settings_tab)
    smalltool_ui = inspect.getsource(jable_smalltool.SmallToolApp._build_ui)
    for source in (modern_ui, smalltool_ui):
        assert "T('max_workers_per_video_setting')" in source
        assert "'max_workers_per_video_desc'" in source

    assert gui_modern.SETTINGS_INLINE_HELP_WRAP <= 620
    assert modern_ui.count(
        'wraplength=SETTINGS_INLINE_HELP_WRAP') == 4
    assert modern_ui.count("justify='left', anchor='w'") >= 4

    class _Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    saved = []

    def _save(value):
        saved.append(value)
        return max(1, min(int(value), 16))

    monkeypatch.setattr(
        gui_modern.config, 'set_max_workers_per_video', _save)
    modern = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    modern._workers_var = _Var('99')
    modern._on_workers_change()

    monkeypatch.setattr(
        jable_smalltool.config, 'set_max_workers_per_video', _save)
    smalltool = jable_smalltool.SmallToolApp.__new__(
        jable_smalltool.SmallToolApp)
    smalltool._workers_var = _Var('2')
    smalltool._on_workers_change()

    assert saved == [99, 2]
    assert modern._workers_var.get() == '16'
    assert smalltool._workers_var.get() == '2'


def test_both_apps_expose_and_persist_filename_mode(monkeypatch):
    modern_ui = inspect.getsource(gui_modern.ModernApp._build_settings_tab)
    watcher_ui = inspect.getsource(jable_smalltool.SmallToolApp._build_ui)
    for source in (modern_ui, watcher_ui):
        assert "T('filename_mode_setting')" in source
        assert "T('filename_mode_desc')" in source
        assert 'command=self._on_filename_mode_change' in source

    class _Var:
        def __init__(self, value=''):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    saved = []

    def _save(value):
        saved.append(value)
        return gui_modern.config.normalize_filename_mode(value)

    monkeypatch.setattr(gui_modern.config, 'set_filename_mode', _save)

    modern = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    modern._filename_mode_var = _Var()
    modern._on_filename_mode_change(modern._filename_mode_values()[1])

    watcher = jable_smalltool.SmallToolApp.__new__(
        jable_smalltool.SmallToolApp)
    watcher._filename_mode_var = _Var()
    watcher._on_filename_mode_change(watcher._filename_mode_values()[1])

    assert saved == ['code-only', 'code-only']
    assert modern._filename_mode_var.get() == modern._filename_mode_values()[1]
    assert watcher._filename_mode_var.get() == watcher._filename_mode_values()[1]


def test_language_rebuild_commits_the_focused_worker_entry(monkeypatch):
    class _Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    saved = []
    monkeypatch.setattr(
        gui_modern.config, 'get_max_workers_per_video', lambda: 16)
    monkeypatch.setattr(
        gui_modern.config, 'set_max_workers_per_video',
        lambda value: saved.append(('modern', value)) or int(value))
    modern = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    modern._workers_var = _Var('3')
    assert modern._commit_workers_preference() == 3
    assert "'max_workers_per_video': self._commit_workers_preference()" in (
        inspect.getsource(gui_modern.ModernApp._apply_language))

    monkeypatch.setattr(
        jable_smalltool.config, 'get_max_workers_per_video', lambda: 16)
    monkeypatch.setattr(
        jable_smalltool.config, 'set_max_workers_per_video',
        lambda value: saved.append(('smalltool', value)) or int(value))
    smalltool = jable_smalltool.SmallToolApp.__new__(
        jable_smalltool.SmallToolApp)
    smalltool._workers_var = _Var('3')
    assert smalltool._commit_workers_preference() == 3
    assert "'max_workers_per_video': self._commit_workers_preference()" in (
        inspect.getsource(jable_smalltool.SmallToolApp._snapshot_ui_state))
    assert "snapshot['max_workers_per_video']" in inspect.getsource(
        jable_smalltool.SmallToolApp._restore_ui_state)

    assert saved == [('modern', 3), ('smalltool', 3)]


def test_supjav_thumbnail_context_reuses_cf_override_only_on_trusted_zone(
        monkeypatch):
    override = {'cookie': 'test-clearance', 'ua': 'Exact Browser UA'}
    monkeypatch.setattr(
        gui_modern.config, 'get_cf_override',
        lambda host: dict(override) if host == 'supjav.com' else None)

    request_headers, cookies = gui_modern._thumbnail_request_context(
        'https://img.supjav.com/thumbs/example.jpg', 'SupJav')

    assert request_headers['User-Agent'] == 'Exact Browser UA'
    assert request_headers['Referer'] == 'https://supjav.com/'
    assert cookies.get_dict(domain='.supjav.com') == {
        'cf_clearance': 'test-clearance',
    }
    trusted = gui_modern.requests.Request(
        'GET', 'https://img.supjav.com/thumb.jpg', cookies=cookies).prepare()
    disguised = gui_modern.requests.Request(
        'GET', 'https://img.supjav.com.evil.example/thumb.jpg',
        cookies=cookies).prepare()
    insecure = gui_modern.requests.Request(
        'GET', 'http://img.supjav.com/thumb.jpg', cookies=cookies).prepare()
    assert trusted.headers['Cookie'] == 'cf_clearance=test-clearance'
    assert 'Cookie' not in disguised.headers
    assert 'Cookie' not in insecure.headers

    external_headers, external_cookies = gui_modern._thumbnail_request_context(
        'https://images.example.test/thumb.jpg', 'SupJav')
    assert external_headers['User-Agent'] == gui_modern.headers['User-Agent']
    assert 'Referer' not in external_headers
    assert external_cookies is None

    for url, site_key in (
            ('https://img.supjav.com.evil.example/thumb.jpg', 'SupJav'),
            ('http://img.supjav.com/thumb.jpg', 'SupJav'),
            ('https://img.supjav.com/thumb.jpg', 'JableTV')):
        untrusted_headers, untrusted_cookies = (
            gui_modern._thumbnail_request_context(url, site_key))
        assert untrusted_headers == gui_modern.headers
        assert untrusted_cookies is None


def test_failed_thumbnail_replaces_loading_placeholder(monkeypatch):
    configured = []

    class _ImmediateExecutor:
        def submit(self, callback):
            callback()

    class _Label:
        def winfo_exists(self):
            return True

        def configure(self, **kwargs):
            configured.append(kwargs)

    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._is_closing = False
    app._grid_gen = 7
    app._build_gen = 9
    app._thumb_executor = _ImmediateExecutor()
    app._ui = lambda callback, gen=None: callback()
    monkeypatch.setattr(gui_modern, '_fetch_thumbnail', lambda *_args: None)

    app._load_thumb_async(
        'https://img.supjav.com/missing.jpg', _Label(), 7, 9, 'SupJav')

    assert configured[-1]['text'] == gui_modern.T('no_thumbnail')


def test_stale_supjav_thumbnail_override_retries_without_credentials(
        monkeypatch):
    calls = []

    class _Response:
        def __init__(self, status_code, content=b''):
            self.status_code = status_code
            self.content = content

        def close(self):
            pass

    class _Session:
        def get(self, url, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return _Response(403)
            return _Response(200, b'image-bytes')

    class _Image:
        size = (300, 169)

        def convert(self, _mode):
            return self

        def thumbnail(self, _size, _resample):
            pass

    monkeypatch.setattr(gui_modern, '_get_thumb_session', lambda: _Session())
    monkeypatch.setattr(gui_modern.Image, 'open', lambda _stream: _Image())
    monkeypatch.setattr(gui_modern, '_thumb_cache', {})
    monkeypatch.setattr(
        gui_modern.config, 'get_cf_override',
        lambda _host: {'cookie': 'stale', 'ua': 'Stale UA'})

    result = gui_modern._fetch_thumbnail(
        'https://img.supjav.com/thumb.jpg', 'SupJav')

    assert result is not None
    assert len(calls) == 2
    assert calls[0]['headers']['User-Agent'] == 'Stale UA'
    assert calls[0]['cookies'].get_dict(domain='.supjav.com') == {
        'cf_clearance': 'stale',
    }
    assert calls[1]['headers'] == gui_modern.headers
    assert 'cookies' not in calls[1]


def test_global_version_selector_saves_internal_preference(monkeypatch):
    app = jable_smalltool.SmallToolApp.__new__(jable_smalltool.SmallToolApp)
    app._cfg = {}
    saved = []
    monkeypatch.setattr(
        jable_smalltool, 'update_config',
        lambda patch, **_kwargs: saved.append(dict(patch)))

    app._on_version_change(jable_smalltool.T('st_pref_uncensored'))

    assert app._cfg['version_preference'] == 'uncensored'
    assert saved[-1]['version_preference'] == 'uncensored'


def test_smalltool_selected_count_reflects_target_vars_only():
    app = jable_smalltool.SmallToolApp.__new__(jable_smalltool.SmallToolApp)
    captured = {}
    app._selected_count_lbl = types.SimpleNamespace(
        configure=lambda **kwargs: captured.update(kwargs))
    app._check_vars = {
        'JableTV|__group__|feeds': types.SimpleNamespace(get=lambda: True),
        'JableTV|feed:latest': types.SimpleNamespace(get=lambda: True),
        'MissAV|feed:latest': types.SimpleNamespace(get=lambda: False),
    }

    app._update_selected_count()

    assert captured['text'].startswith('1 ')
    assert captured['text_color'] is ui_theme.ACCENT
