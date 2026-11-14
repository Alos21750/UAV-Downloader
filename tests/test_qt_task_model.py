"""Optional Qt tests: install PySide6 to exercise the production model."""
from dataclasses import replace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QModelIndex, QPersistentModelIndex
from uav_downloader.core.task_queue import DownloadManager
from uav_downloader.ui.qt.task_model import DownloadListModel


def test_progress_updates_keep_model_rows_and_only_notify_changed_roles():
    application = QCoreApplication.instance() or QCoreApplication([])
    manager = DownloadManager()
    manager.add_item('https://example.test/a', state='下載中')
    manager.add_item('https://example.test/b', state='等待中')
    initial = manager.snapshot_items()
    model = DownloadListModel()
    model.refresh(initial)
    resets, changes = [], []
    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(lambda first, last, roles: changes.append((first.row(), last.row(), roles)))
    model.refresh(initial)
    assert changes == []
    model.refresh((replace(initial[0], progress=30), initial[1]))
    assert resets == []
    progress_role = next(role for role, field in model.ROLES.items() if field == b'progress')
    assert changes == [(0, 0, [progress_role])]
    assert model.data(model.index(0), progress_role) == 30
    assert model.data(QModelIndex(), progress_role) is None
    assert model.rowCount(model.index(0)) == 0
    model.refresh(())
    assert resets == [] and model.rowCount() == 0


def test_source_notice_role_changes_without_resetting_delegate():
    application = QCoreApplication.instance() or QCoreApplication([])
    manager = DownloadManager()
    item = manager.add_item('https://supjav.com/123.html', state='下載中')
    model = DownloadListModel()
    model.refresh(manager.snapshot_items())
    resets, changes = [], []
    model.modelReset.connect(lambda: resets.append(True))
    model.dataChanged.connect(lambda first, last, roles: changes.append(roles))
    model.refresh((replace(model._rows[0], source_kind='direct', source_fallback=True, resume_hint='full_response'),))
    changed = {model.ROLES[role].decode() for roles in changes for role in roles}
    assert changed == {'source_kind', 'source_fallback', 'resume_hint'}
    assert not resets


def test_append_and_remove_preserve_unaffected_task_identity():
    application = QCoreApplication.instance() or QCoreApplication([])
    manager = DownloadManager()
    for url in ('a', 'b'):
        manager.add_item(url)
    model = DownloadListModel()
    model.refresh(manager.snapshot_items())
    reference = QPersistentModelIndex(model.index(1))
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    manager.add_item('c')
    model.refresh(manager.snapshot_items())
    manager.remove_item('a')
    model.refresh(manager.snapshot_items())
    assert reference.isValid() and reference.row() == 0
    url_role = next(role for role, field in model.ROLES.items() if field == b'url')
    assert reference.data(url_role) == 'b'
    assert model.rowCount() == 2 and resets == []


def test_adjacent_progress_notifications_are_batched_but_unchanged_rows_are_excluded():
    application = QCoreApplication.instance() or QCoreApplication([])
    manager = DownloadManager()
    for index in range(1000):
        manager.add_item(str(index))
    model = DownloadListModel()
    initial = manager.snapshot_items()
    model.refresh(initial)
    changes = []
    model.dataChanged.connect(lambda first, last, roles: changes.append((first.row(), last.row(), roles)))
    progress_role = next(role for role, field in model.ROLES.items() if field == b'progress')
    updated = tuple(replace(item, progress=20) if index != 500 else item for index, item in enumerate(initial))
    model.refresh(updated)
    assert changes == [(0, 499, [progress_role]), (501, 999, [progress_role])]
