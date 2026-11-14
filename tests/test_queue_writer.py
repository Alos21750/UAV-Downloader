import csv
import threading

import pytest

from uav_downloader.apps.browse import DownloadManager


def test_queue_snapshot_does_not_follow_later_item_mutations(tmp_path):
    manager = DownloadManager()
    item = manager.add_item('https://example.test/video', state='下載中')
    item.progress = 10
    snapshot = manager.snapshot_csv()
    item.progress = 90
    manager.clear_all()
    assert snapshot[1][0] == '下載中'
    assert snapshot[1][2] == '10%'
    assert len(snapshot) == 2


def test_slow_save_never_publishes_over_a_newer_clear(monkeypatch, tmp_path):
    from uav_downloader.core import queue_store
    path = tmp_path / 'queue.csv'
    path.write_text('original\n', encoding='utf-8')
    entered, release = threading.Event(), threading.Event()
    real_fsync = queue_store.os.fsync
    calls = []

    def slow_fsync(fd):
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        real_fsync(fd)

    monkeypatch.setattr(queue_store.os, 'fsync', slow_fsync)
    writer = queue_store.QueueSnapshotWriter(str(path))
    try:
        old = writer.submit((('header',), ('old task',)))
        assert entered.wait(2)
        cleared = writer.submit((('header',),))
        # Submission and the UI caller are not waiting for the blocked disk.
        assert not old.done() and not cleared.done()
        assert path.read_text(encoding='utf-8') == 'original\n'
        release.set()
        assert old.result(3) is False  # stale snapshot must not be published
        assert cleared.result(3) is True
        assert all(ident != threading.get_ident() for ident in calls)
        with path.open(encoding='utf-8') as handle:
            assert list(csv.reader(handle)) == [['header']]
    finally:
        release.set()
        writer.close()
        assert writer.wait_closed(3)
    assert list(tmp_path.iterdir()) == [path]


def test_save_failure_is_observable_and_retry_succeeds(monkeypatch, tmp_path):
    from uav_downloader.core import queue_store
    path = tmp_path / 'queue.csv'
    path.write_text('original\n', encoding='utf-8')
    real_replace = queue_store.os.replace
    writer = queue_store.QueueSnapshotWriter(str(path))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(queue_store.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk error')))
            with pytest.raises(OSError, match='disk error'):
                writer.submit((('header',),)).result(3)
        assert path.read_text(encoding='utf-8') == 'original\n'
        assert writer.submit((('new',),)).result(3) is True
        assert queue_store.os.replace is real_replace
    finally:
        writer.close()
        assert writer.wait_closed(3)
    assert list(tmp_path.iterdir()) == [path]


def test_close_flushes_last_snapshot_and_rejects_new_work(tmp_path):
    from uav_downloader.core.queue_store import QueueSnapshotWriter
    path = tmp_path / 'queue.csv'
    writer = QueueSnapshotWriter(str(path))
    final = writer.submit((('last state',),))
    writer.close()
    assert writer.wait_closed(3)
    assert final.result() is True
    with pytest.raises(RuntimeError):
        writer.submit(())
    assert path.read_text(encoding='utf-8').strip() == 'last state'


def test_slow_replace_does_not_block_ui_submission(monkeypatch, tmp_path):
    from uav_downloader.core import queue_store
    entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
    real_replace = queue_store.os.replace
    calls = []

    def slow_replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        real_replace(source, target)

    monkeypatch.setattr(queue_store.os, 'replace', slow_replace)
    path = tmp_path / 'queue.csv'
    writer = queue_store.QueueSnapshotWriter(str(path))
    latest = []
    def submit():
        latest.append(writer.submit((('latest',),)))
        submitted.set()

    thread = threading.Thread(target=submit)
    try:
        writer.submit((('old',),))
        assert entered.wait(2)
        thread.start()
        assert submitted.wait(1), 'UI submit blocked behind disk replacement'
        release.set()
        assert latest[0].result(3)
        assert path.read_text(encoding='utf-8').strip() == 'latest'
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(3)
        writer.close()
        assert writer.wait_closed(3)
