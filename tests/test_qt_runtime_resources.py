"""Qt resources must ship from a clean checkout without installing the project."""
from pathlib import Path
import runpy
import shutil
import pytest


def test_qml_resources_are_collected_without_an_importable_project(monkeypatch, tmp_path):
    hooks = pytest.importorskip('PyInstaller.utils.hooks')
    repository = Path(__file__).resolve().parents[1]
    checkout = tmp_path / 'uninstalled checkout'
    specs = checkout / 'packaging/windows'
    specs.mkdir(parents=True)
    source = checkout / 'src/uav_downloader/ui/qt'
    source.mkdir(parents=True)
    for path in (repository / 'src/uav_downloader/ui/qt').glob('*.qml'):
        shutil.copyfile(path, source / path.name)
    shutil.copytree(repository / 'src/uav_downloader/ui/qt/icons', source / 'icons')
    # No __init__.py or installed distribution exists in this fixture.
    monkeypatch.setattr(hooks, 'collect_all', lambda unused: ([], [], []))
    monkeypatch.setattr(hooks, 'copy_metadata', lambda unused: [])
    def no_project_import(package, *args, **kwargs):
        assert not package.startswith('uav_downloader'), 'Project resource discovery must not depend on imports'
        return []
    monkeypatch.setattr(hooks, 'collect_data_files', no_project_import)
    module = runpy.run_path(str(repository / 'packaging/windows/qt_runtime.py'))
    data, binaries, hidden = module['qt_runtime'](specs)
    packaged = {Path(path).name: Path(path).read_bytes() for path, dest in data if dest == 'uav_downloader/ui/qt'}
    expected = {path.name: path.read_bytes() for path in source.glob('*.qml')}
    assert packaged == expected
    assert {'Tasks.qml', 'CalendarDialog.qml', 'WatcherPane.qml', 'SettingsPane.qml'} <= packaged.keys()
    icons = {Path(path).name: Path(path).read_bytes() for path, dest in data if dest == 'uav_downloader/ui/qt/icons'}
    assert icons == {path.name: path.read_bytes() for path in (source / 'icons').glob('*.svg')}
    assert set(icons) == {'sun.svg', 'moon.svg'}
