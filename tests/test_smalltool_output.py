import json

from uav_downloader.apps import watch as jable_smalltool


def test_blank_saved_folder_falls_back_to_tmp_beside_app(monkeypatch, tmp_path):
    app_dir = tmp_path / 'portable'
    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    config_path = state_dir / 'config.json'
    config_path.write_text(json.dumps({
        'output_folder': '',
        'selected_targets': [],
    }), encoding='utf-8')

    monkeypatch.setattr(jable_smalltool, 'APP_DIR', str(app_dir))
    monkeypatch.setattr(jable_smalltool, 'STATE_DIR', str(state_dir))
    monkeypatch.setattr(jable_smalltool, 'CONFIG_PATH', str(config_path))

    cfg = jable_smalltool.load_config()

    assert cfg['output_folder'] == str(app_dir / 'tmp')
    assert cfg['version_preference'] == 'chinese-subtitle'
    assert 'missav_version_preference' not in cfg


def test_missing_config_uses_tmp_beside_app(monkeypatch, tmp_path):
    app_dir = tmp_path / 'portable'
    state_dir = tmp_path / 'state'
    monkeypatch.setattr(jable_smalltool, 'APP_DIR', str(app_dir))
    monkeypatch.setattr(jable_smalltool, 'STATE_DIR', str(state_dir))
    monkeypatch.setattr(
        jable_smalltool, 'CONFIG_PATH', str(state_dir / 'missing.json'))

    cfg = jable_smalltool.load_config()

    assert cfg['output_folder'] == str(app_dir / 'tmp')
    assert cfg['version_preference'] == 'chinese-subtitle'


def test_old_missav_preference_is_migrated_to_global_setting(
        monkeypatch, tmp_path):
    state_dir = tmp_path / 'state'
    state_dir.mkdir()
    config_path = state_dir / 'config.json'
    config_path.write_text(json.dumps({
        'output_folder': str(tmp_path),
        'missav_version_preference': 'uncensored-leak',
    }), encoding='utf-8')
    monkeypatch.setattr(jable_smalltool, 'STATE_DIR', str(state_dir))
    monkeypatch.setattr(jable_smalltool, 'CONFIG_PATH', str(config_path))

    cfg = jable_smalltool.load_config()

    assert cfg['version_preference'] == 'uncensored'
    assert 'missav_version_preference' not in cfg


def test_site_settings_default_to_global_values_and_allow_partial_overrides():
    cfg = jable_smalltool._normalize_loaded_config({
        'output_folder': 'D:/UAV/default',
        'baseline_date': '2026-08-01',
        'site_settings': {
            'JableTV': {'output_folder': 'D:/UAV/jable'},
            'MissAV': {'baseline_date': '2026-07-01'},
            'Unknown': {'output_folder': 'D:/UAV/unknown'},
            'SupJav': {'baseline_date': 'not-a-date'},
        },
    })

    assert cfg['site_settings'] == {
        'JableTV': {'output_folder': 'D:/UAV/jable'},
        'MissAV': {'baseline_date': '2026-07-01'},
    }
    assert jable_smalltool._resolve_site_settings(cfg, 'JableTV') == {
        'output_folder': 'D:/UAV/jable',
        'baseline_date': '2026-08-01',
    }
    assert jable_smalltool._resolve_site_settings(cfg, 'MissAV') == {
        'output_folder': 'D:/UAV/default',
        'baseline_date': '2026-07-01',
    }
    assert jable_smalltool._resolve_site_settings(cfg, 'Hanime1') == {
        'output_folder': 'D:/UAV/default',
        'baseline_date': '2026-08-01',
    }


def test_legacy_config_without_site_settings_keeps_identical_site_defaults():
    cfg = jable_smalltool._normalize_loaded_config({
        'output_folder': 'D:/UAV/shared',
        'baseline_date': '2026-08-03',
    })

    assert cfg['site_settings'] == {}
    for site_name in jable_smalltool.SITES:
        assert jable_smalltool._resolve_site_settings(cfg, site_name) == {
            'output_folder': 'D:/UAV/shared',
            'baseline_date': '2026-08-03',
        }
