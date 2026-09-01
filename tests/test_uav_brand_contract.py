"""Public identity, compatibility, and migration contracts for UAV v3.1."""

from pathlib import Path

from uav_downloader import metadata
from uav_downloader.core import crashlog
from uav_downloader.core import paths
from uav_downloader.i18n import locales


ROOT = Path(__file__).resolve().parents[1]


def test_product_identity_and_release_channels_are_centralized():
    assert metadata.PRODUCT_NAME == "UAV Downloader"
    assert metadata.VERSION == "3.1.4"
    assert metadata.GITHUB_REPOSITORY == "Alos21750/UAV-Downloader"
    assert metadata.LEGACY_GITHUB_REPOSITORIES == (
        "Alos21750/ALOS-Unified-AV-Downloader",
        "Alos21750/JableTV-MissAV-Downloader-GUI-2026",
    )
    assert metadata.APP_DISPLAY_NAMES == {
        "browse": "UAV Browser",
        "watch": "UAV Watcher",
        "headless": "UAV Downloader CLI",
    }


def test_release_assets_are_uav_first_with_two_transition_aliases():
    assert metadata.release_asset_candidates("browse") == (
        "UAV_Browser.exe",
        "ALOS_Browse.exe",
        "JableTV_Modern.exe",
    )
    assert metadata.release_asset_candidates("watch") == (
        "UAV_Watcher.exe",
        "ALOS_Watch.exe",
        "Jable_smalltool.exe",
    )
    assert metadata.release_asset_candidates("watch-portable") == (
        "UAV_Watcher_portable.zip",
        "ALOS_Watch_portable.zip",
        "Jable_smalltool_portable.zip",
    )
    assert metadata.release_asset_candidates("asr-pack") == (
        "UAV_reazonspeech_asr_v1.zip",
        "ALOS_reazonspeech_asr_v1.zip",
        "Jable_reazonspeech_asr_v1.zip",
    )


def test_both_legacy_user_data_trees_migrate_copy_only(tmp_path):
    alos = tmp_path / "ALOS Unified AV Downloader"
    jable = tmp_path / "JableTV Downloader"
    current = tmp_path / metadata.PRODUCT_NAME
    alos.mkdir()
    jable.mkdir()
    current.mkdir()
    (alos / "ui_prefs.json").write_text("alos-prefs", encoding="utf-8")
    (jable / "queue.csv").write_text("jable-queue", encoding="utf-8")
    (current / "ui_prefs.json").write_text("uav-prefs", encoding="utf-8")

    selected = paths.product_data_dir(tmp_path)

    assert selected == current
    assert (current / "ui_prefs.json").read_text(encoding="utf-8") == "uav-prefs"
    assert (current / "queue.csv").read_text(encoding="utf-8") == "jable-queue"
    assert (alos / "ui_prefs.json").is_file()
    assert (jable / "queue.csv").is_file()


def test_generic_migration_never_overwrites_existing_data(tmp_path):
    legacy = tmp_path / "legacy"
    current = tmp_path / "current"
    legacy.mkdir()
    current.mkdir()
    (legacy / "same.txt").write_text("legacy", encoding="utf-8")
    (legacy / "missing.txt").write_text("copy-me", encoding="utf-8")
    (current / "same.txt").write_text("current", encoding="utf-8")

    report = paths.migrate_directory(legacy, current)

    assert (current / "same.txt").read_text(encoding="utf-8") == "current"
    assert (current / "missing.txt").read_text(encoding="utf-8") == "copy-me"
    assert report.copied_files == 1
    assert report.skipped_files == 1


def test_both_portable_state_names_migrate_to_uav_watcher(tmp_path):
    alos = tmp_path / ".alos-watch"
    jable = tmp_path / ".Jable_smalltool"
    current = tmp_path / ".uav-watcher"
    alos.mkdir()
    jable.mkdir()
    (alos / "config.json").write_text("alos", encoding="utf-8")
    (jable / "seen.json").write_text("jable", encoding="utf-8")

    selected = paths.select_portable_state_dir(tmp_path)

    assert selected == current
    assert (current / "config.json").read_text(encoding="utf-8") == "alos"
    assert (current / "seen.json").read_text(encoding="utf-8") == "jable"
    assert (alos / "config.json").is_file()
    assert (jable / "seen.json").is_file()


def test_runtime_source_and_entrypoints_use_only_the_uav_namespace():
    assert (ROOT / "src" / "uav_downloader" / "__init__.py").is_file()
    assert not (ROOT / "src" / "alos_downloader").exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "uav-downloader"' in pyproject
    assert 'uav-browser = "uav_downloader.entrypoints.browse:main"' in pyproject
    assert 'uav-watcher = "uav_downloader.entrypoints.watch:main"' in pyproject
    assert "alos =" not in pyproject.lower()
    assert list(ROOT.glob("*.py")) == []


def test_visible_runtime_branding_contains_uav_and_not_alos():
    source = (ROOT / "src" / "uav_downloader" / "core" / "crashlog.py").read_text(
        encoding="utf-8"
    )
    browse = (ROOT / "src" / "uav_downloader" / "apps" / "browse.py").read_text(
        encoding="utf-8"
    )
    assert crashlog._app_version() == metadata.VERSION
    assert "ALOS — Crash" not in source
    assert "INTERACTIVE DOWNLOADER  /  ALOS" not in browse
    assert "UAV DOWNLOADER  /  BROWSER" in browse
    for strings in locales.STRINGS.values():
        for key in (
            "app_brand_1",
            "app_full_name",
            "st_header",
            "st_window_title",
            "st_version_by",
        ):
            assert "ALOS" not in strings[key]


def test_canonical_windows_packaging_uses_uav_names():
    packaging = ROOT / "packaging" / "windows"
    for name in (
        "UAV_Browser.spec",
        "UAV_Browser.version",
        "UAV_Watcher.spec",
        "UAV_Watcher.version",
    ):
        assert (packaging / name).is_file()
    assert not list(packaging.glob("ALOS_*.spec"))
    assert not list(packaging.glob("ALOS_*.version"))
