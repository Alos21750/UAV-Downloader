"""Global toggling respects effective appearance and atomic preference saves."""
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication
from uav_downloader.core import config
from uav_downloader.core.task_service import TaskService
from uav_downloader.ui.qt.settings_controller import SettingsController


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(tmp_path / 'prefs.json'))
    config._save_prefs(dict(theme='system', lang='ja', filename_mode='code-only'))
    service = TaskService(str(tmp_path / 'queue.csv'))
    service.ready.result(3)
    controller = SettingsController(service); controller.timer.stop()
    while controller.busy:
        controller._pending[1].result(3)
        controller.poll()
    original_write = config._save_prefs
    try:
        yield controller
    finally:
        monkeypatch.setattr(config, '_save_prefs', original_write)
        controller.abandon()
        try:
            service.close().result(3)
        except OSError:
            service.close().result(3)  # Explicit retry of the injected failed write.
        finally:
            service.abandon()


@pytest.mark.parametrize('initial,effective_dark,expected', [
    ('system', True, 'light'), ('system', False, 'dark'),
    ('dark', True, 'light'), ('light', False, 'dark')])
def test_toggle_uses_effective_appearance_and_keeps_other_preferences(adapter, initial, effective_dark, expected):
    adapter.save(dict(theme=initial)); adapter._pending[1].result(3); adapter.poll()
    saved = []
    adapter.groupSaved.connect(saved.append)
    adapter.toggleTheme(effective_dark)
    assert adapter.busyGroup == 'theme' and not saved
    adapter._pending[1].result(3); adapter.poll()
    assert config.get_theme() == adapter.values['theme'] == expected
    assert saved == ['theme']
    assert config.get_ui_lang() == 'ja' and config.get_filename_mode() == 'code-only'


def test_failed_toggle_keeps_applied_system_mode(adapter, monkeypatch):
    errors, saved = [], []
    adapter.groupError.connect(lambda group, message: errors.append((group, message)))
    adapter.groupSaved.connect(saved.append)
    def fail(values): raise OSError('theme write failed')
    monkeypatch.setattr(config, '_save_prefs', fail)
    adapter.toggleTheme(True)
    with pytest.raises(OSError): adapter._pending[1].result(3)
    adapter.poll()
    assert not saved and errors == [('theme', 'theme write failed')]
    assert adapter.values['theme'] == config.get_theme() == 'system'
