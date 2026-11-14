from concurrent.futures import Future
from types import SimpleNamespace
import queue

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication
from uav_downloader.ui.qt.watcher_controller import WatcherController


def test_filtered_selection_maps_to_site_and_preserves_other_site_selection():
    app = QCoreApplication.instance() or QCoreApplication([])
    ready = Future()
    ready.set_result(dict(selected_targets=[]))
    controller = WatcherController('', service=SimpleNamespace(ready=ready, worker=None, events=queue.SimpleQueue()))
    controller.timer.stop()
    controller.poll()
    controller.setFilter('JableTV', 'feed:latest')
    assert controller.targets.rowCount() == 1
    controller.select(0, True)
    controller.setFilter('MissAV', 'feed:latest')
    assert controller.targets.rowCount() == 1
    controller.selectVisible(True)
    assert {(row['site'], row['id']) for row in controller.model.selection()} == {
        ('JableTV', 'feed:latest'), ('MissAV', 'feed:latest')}
    controller.selectVisible(False)
    assert controller.model.selection() == [dict(site='JableTV', id='feed:latest')]
    controller.setFilter('SupJav', 'nonexistent-category')
    controller.selectVisible(True)
    assert len(controller.model.selection()) == 1


def test_close_preserves_error_from_pending_monitor_save_and_waits_for_worker():
    app = QCoreApplication.instance() or QCoreApplication([])
    ready, save, stopping = Future(), Future(), Future()
    ready.set_result(dict(selected_targets=[]))
    service = SimpleNamespace(ready=ready, worker=None, events=queue.SimpleQueue(),
                              submit=lambda *args: save, close=lambda: stopping)
    controller = WatcherController('', service=service)
    controller.timer.stop()
    controller.poll()
    controller.apply(dict(baseline_date='2026-01-01'), 'save')
    closed = controller.close()
    save.set_exception(OSError('Watcher disk full'))
    controller.poll()
    assert not closed.done()
    stopping.set_result(True)
    with pytest.raises(OSError, match='Watcher disk full'):
        closed.result(0)
