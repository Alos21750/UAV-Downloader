from concurrent.futures import Future
from types import SimpleNamespace
import queue

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication
from uav_downloader.ui.qt.task_controller import TaskController


def test_close_timeout_requires_explicit_choice_and_retry_keeps_waiting():
    application = QCoreApplication.instance() or QCoreApplication([])
    ready, closing = Future(), Future()
    ready.set_result(True)
    discarded = []
    service = SimpleNamespace(
        ready=ready, errors=queue.SimpleQueue(),
        manager=SimpleNamespace(snapshot_items=lambda: ()),
        close=lambda: closing, abandon=lambda: discarded.append(True))
    controller = TaskController('', service=service)
    controller.timer.stop()
    decisions, exits = [], []
    controller.closeNeedsDecision.connect(decisions.append)
    controller.closeReady.connect(lambda: exits.append(True))
    controller.poll()
    assert controller.ready
    controller.requestClose()
    controller._close_deadline = 0
    controller.poll()
    controller.poll()
    assert len(decisions) == 1 and exits == [] and discarded == []
    controller.requestClose()
    controller.poll()
    assert exits == []
    closing.set_result(True)
    controller.poll()
    assert exits == [True] and discarded == []


def test_failed_close_never_silently_exits():
    application = QCoreApplication.instance() or QCoreApplication([])
    ready, closing = Future(), Future()
    ready.set_result(True)
    closing.set_exception(OSError('disk full'))
    discarded = []
    service = SimpleNamespace(
        ready=ready, errors=queue.SimpleQueue(),
        manager=SimpleNamespace(snapshot_items=lambda: ()),
        close=lambda: closing, abandon=lambda: discarded.append(True))
    controller = TaskController('', service=service)
    controller.timer.stop()
    errors, exits = [], []
    controller.closeNeedsDecision.connect(errors.append)
    controller.closeReady.connect(lambda: exits.append(True))
    controller.requestClose()
    controller.poll()
    assert errors == ['disk full'] and exits == []
    controller.discardAndClose()
    assert discarded == [True] and exits == [True]


def test_close_waits_for_watcher_and_explicit_discard_stops_both():
    application = QCoreApplication.instance() or QCoreApplication([])
    ready, tasks_closed, watcher_closed = Future(), Future(), Future()
    ready.set_result(True)
    tasks_closed.set_result(True)
    discarded = []
    service = SimpleNamespace(ready=ready, errors=queue.SimpleQueue(),
        manager=SimpleNamespace(snapshot_items=lambda: ()), close=lambda: tasks_closed,
        abandon=lambda: discarded.append('tasks'))
    controller = TaskController('', service=service)
    controller.timer.stop()
    controller.addCloseService(SimpleNamespace(close=lambda: watcher_closed,
        abandon=lambda: discarded.append('watcher')))
    exits = []
    controller.closeReady.connect(lambda: exits.append(True))
    controller.requestClose()
    controller.poll()
    assert exits == []
    controller.discardAndClose()
    assert discarded == ['tasks', 'watcher'] and exits == [True]
