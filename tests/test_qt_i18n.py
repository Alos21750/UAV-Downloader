import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, QCoreApplication, QPersistentModelIndex, Signal
from uav_downloader.i18n.locales import get_lang, set_lang
from uav_downloader.ui.qt.i18n_controller import I18nController
from uav_downloader.ui.qt.watcher_controller import WatchTargetModel


@pytest.mark.parametrize('language,label,done', [
    ('en', 'Tasks', 'Done'), ('zh', '任務', '已完成'),
    ('zh-Hans', '任务', '已完成'), ('ja', 'タスク', '完了')])
def test_language_change_updates_labels_and_preserves_selected_category_index(language, label, done):
    app = QCoreApplication.instance() or QCoreApplication([])
    previous = get_lang()
    class Settings(QObject):
        valuesChanged = Signal()
        values = dict(lang='en')
    try:
        settings = Settings()
        i18n = I18nController(settings)
        model = WatchTargetModel()
        model.select(0, True)
        persistent = QPersistentModelIndex(model.index(0))
        selected = model.selection()
        i18n.changed.connect(model.retranslate)
        settings.values = dict(lang=language)
        settings.valuesChanged.emit()
        assert i18n.strings['qt_tasks'] == label
        assert i18n.states['已下載'] == done
        assert i18n.options['chinese-subtitle'] != 'chinese-subtitle'
        assert persistent.isValid() and persistent.row() == 0
        assert model.selection() == selected
    finally:
        set_lang(previous)


def test_existing_qml_task_state_changes_language_without_resetting_task_rows():
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    import shiboken6
    from uav_downloader.core.task_queue import DownloadManager
    from uav_downloader.ui.qt.task_model import DownloadListModel
    app = QCoreApplication.instance() or QCoreApplication([])
    previous = get_lang()
    class Settings(QObject):
        valuesChanged = Signal()
        values = dict(lang='en')
    settings = Settings()
    i18n = I18nController(settings)
    manager = DownloadManager()
    manager.add_item('https://example.test/done', state='已下載')
    model = DownloadListModel()
    model.refresh(manager.snapshot_items())
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    engine = QQmlEngine()
    engine.rootContext().setContextProperty('i18n', i18n)
    engine.rootContext().setContextProperty('tasks', model)
    component = QQmlComponent(engine)
    component.setData(b'''import QtQml
import QtQml.Models
Instantiator {
    model: tasks
    property var labelObject: count > 0 ? objectAt(0) : null
    delegate: QtObject {
        required property string state
        property string rendered: i18n.states[state] || state
    }
}''', QUrl())
    try:
        root = component.create()
        assert root is not None, [error.toString() for error in component.errors()]
        delegate = root.property('labelObject')
        assert delegate.property('rendered') == 'Done'
        settings.values = dict(lang='ja')
        settings.valuesChanged.emit()
        app.processEvents()
        assert delegate.property('rendered') == '完了' and not resets
    finally:
        shiboken6.delete(engine)
        set_lang(previous)
