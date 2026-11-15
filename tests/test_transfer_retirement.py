"""Retirement/cleanup boundaries through official Tk, Watcher and CLI paths."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from uav_downloader.apps import browse, watch
from uav_downloader.cli import headless
from uav_downloader import sites
from uav_downloader.sites import base
from uav_downloader.core.storage import StorageWriteError


def eventually(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail('Controlled worker did not reach the expected boundary')
        threading.Event().wait(.005)


def bare(tmp_path):
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._t_executor = job._t2_executor = job._t_future = job._ffmpeg_proc = None
    job._temp_folder = str(tmp_path / 'cache')
    job._dest_folder = str(tmp_path)
    job._targetName = 'controlled'
    job._tsList = []
    Path(job._temp_folder).mkdir(exist_ok=True)
    return job


def controlled_transfer(job, entered, release):
    job._create_dest_folder = job.download_image = job._create_temp_folder = job._create_m3u8 = lambda: None
    job.is_target_video_exist = lambda: False
    job._pending_set = set()
    def transfer():
        entered.set()
        assert release.wait(5)
    job._prepareCrawl = transfer


@pytest.mark.parametrize('cleanup', [True, False])
def test_cancel_keeps_live_cache_until_transfer_returns(tmp_path, cleanup):
    job = bare(tmp_path)
    cached = Path(job._temp_folder) / 'segment'
    cached.write_bytes(b'prior validated segment')
    entered, release = threading.Event(), threading.Event()
    controlled_transfer(job, entered, release)
    results = []
    worker = threading.Thread(target=lambda: results.append(job.start_download()))
    try:
        worker.start()
        assert entered.wait(2)
        job.cancel_download(cleanup=cleanup)
        assert cached.read_bytes() == b'prior validated segment'
        assert worker.is_alive()
        release.set()
        worker.join(2)
        assert results == [False]
        if cleanup:
            eventually(lambda: not cached.exists())
        else:
            assert cached.exists()
    finally:
        release.set()
        worker.join(3)
        wait = getattr(job, 'wait_stopped', None)
        if wait:
            assert wait(3)


def test_cancel_keeps_executor_cache_until_actual_thread_exit(tmp_path):
    job = bare(tmp_path)
    cached = Path(job._temp_folder) / 'segment'
    cached.write_bytes(b'owned cache')
    entered, release = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    def write():
        entered.set()
        assert release.wait(5)
        assert cached.read_bytes() == b'owned cache'
    future = pool.submit(write)
    job._t2_executor = pool
    try:
        assert entered.wait(2)
        job.cancel_download()
        assert cached.exists(), 'Cancellation deleted a cache while its writer still runs'
        assert job._t2_executor is pool, 'Cancellation lost the live executor handle'
        assert not job.wait_stopped(.02)
        release.set()
        assert job.wait_stopped(2)
        future.result(1)
        assert not cached.exists()
        assert not any(t.is_alive() for t in pool._threads)
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_cancel_before_background_body_enters_keeps_reservation(tmp_path, monkeypatch):
    job = bare(tmp_path)
    parked, release = threading.Event(), threading.Event()
    original = job.start_download
    def delayed():
        parked.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(job, 'start_download', delayed)
    try:
        job.begin_concurrent_download()
        assert parked.wait(2)
        job.cancel_download(cleanup=False)
        assert not job.wait_stopped(.02)
        release.set()
        assert job.wait_stopped(2)
        assert job._t_future.result() is False
        assert job.is_concurrent_dowload_completed()
    finally:
        release.set()
        job.wait_stopped(3)


def test_cancelled_queued_future_releases_reservation(tmp_path, monkeypatch):
    job = bare(tmp_path)
    entered, release = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    busy = pool.submit(lambda: (entered.set(), release.wait(5)))
    monkeypatch.setattr(base.concurrent.futures, 'ThreadPoolExecutor', lambda **kw: pool)
    try:
        assert entered.wait(2)
        job.begin_concurrent_download()
        job.cancel_download(cleanup=False)
        assert job._t_future.cancelled()
        assert not job.wait_stopped(.02)
        release.set()
        assert job.wait_stopped(2) and busy.done()
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_storage_failure_revokes_earlier_cleanup_request(tmp_path):
    job = bare(tmp_path)
    cached = Path(job._temp_folder) / 'segment'
    cached.write_bytes(b'recoverable bytes')
    entered, release = threading.Event(), threading.Event()
    controlled_transfer(job, entered, release)
    errors = []
    def fail_storage():
        entered.set()
        assert release.wait(5)
        raise StorageWriteError(str(cached), OSError(28, 'Controlled disk full'))
    job._prepareCrawl = fail_storage
    def run():
        try:
            job.start_download()
        except StorageWriteError as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    try:
        worker.start()
        assert entered.wait(2)
        job.cancel_download(cleanup=True)
        release.set()
        worker.join(2)
        assert len(errors) == 1 and job.wait_stopped(2)
        job.cleanup_temp()
        assert job.wait_stopped(1)
        assert cached.read_bytes() == b'recoverable bytes'
    finally:
        release.set()
        worker.join(3)


def test_future_done_still_waits_for_its_executor_callback(tmp_path):
    job = bare(tmp_path)
    entered, release, callback_entered, callback_release = [threading.Event() for _ in range(4)]
    controlled_transfer(job, entered, release)
    job._mergeMp4Chunks = lambda: 1
    def callback(future):
        callback_entered.set()
        assert callback_release.wait(5)
    try:
        job.begin_concurrent_download()
        assert entered.wait(2)
        job._t_future.add_done_callback(callback)
        release.set()
        assert callback_entered.wait(2)
        assert job._t_future.done()
        assert not job.is_concurrent_dowload_completed()
        callback_release.set()
        assert job.wait_stopped(2)
        assert job.is_concurrent_dowload_completed()
    finally:
        release.set()
        callback_release.set()
        job.wait_stopped(3)


@pytest.mark.parametrize('legacy', [True, False])
def test_manager_requests_stop_without_repeating_cleanup(legacy):
    calls = []
    class Job:
        def cleanup_temp(self):
            calls.append('cleanup')
    job = Job()
    if legacy:
        job.cancel_download = lambda: calls.append('request')
    else:
        def cancel(cleanup=True):
            calls.append('request')
            if cleanup:
                job.cleanup_temp()
        job.cancel_download = cancel
    task = SimpleNamespace(cancelled=threading.Event(), monitor_stop=threading.Event(), job=job)
    browse.DownloadManager._cancel_contexts([task, task])
    assert calls == (['request'] if legacy else ['request', 'cleanup'])


def test_cancel_callback_internal_type_error_is_not_called_twice():
    calls = []
    def cancel(cleanup=True):
        calls.append(cleanup)
        raise TypeError('Controlled internal callback error')
    task = SimpleNamespace(cancelled=threading.Event(), monitor_stop=threading.Event(),
                           job=SimpleNamespace(cancel_download=cancel))
    browse.DownloadManager._cancel_contexts([task], cleanup=False)
    assert calls == [False]


def test_stopped_fast_move_preserves_source_and_existing_target(tmp_path):
    job = bare(tmp_path)
    source, target = tmp_path / 'new', tmp_path / 'existing'
    source.write_bytes(b'new media')
    target.write_bytes(b'user file')
    job._cancel_job = True
    assert job._cancellable_move(str(source), str(target)) is False
    assert source.read_bytes() == b'new media'
    assert target.read_bytes() == b'user file'


def test_stopped_merge_preserves_existing_output_and_partial(tmp_path):
    job = bare(tmp_path)
    final, partial = tmp_path / 'controlled.mp4', tmp_path / 'controlled.mp4.part'
    final.write_bytes(b'existing user video')
    partial.write_bytes(b'prior partial')
    job._cancel_job = True
    assert not job._mergeMp4Chunks()
    assert final.read_bytes() == b'existing user video'
    assert partial.read_bytes() == b'prior partial'
    assert not list(tmp_path.glob('uav-remux-*'))


def test_stop_during_ffmpeg_lookup_does_not_launch_child(tmp_path, monkeypatch):
    job = bare(tmp_path)
    launches = []
    def locate():
        job._cancel_job = True
        return 'controlled-child'
    monkeypatch.setattr(base, 'locate_ffmpeg', locate)
    monkeypatch.setattr(base.subprocess, 'Popen', lambda *a, **kw: launches.append(True))
    assert job._remux_to_mp4('in.ts', 'out.mp4', str(tmp_path)) is False
    assert launches == []


def test_failed_child_wait_does_not_release_ownership_or_workdir(tmp_path, monkeypatch):
    job = bare(tmp_path)
    (Path(job._temp_folder) / '000000.mp4').write_bytes(b'fixture TS')
    job._tsList = ['https://example.test/segment']
    waiting, release, done = threading.Event(), threading.Event(), threading.Event()
    workdirs, errors = [], []
    class Child:
        returncode = None
        def kill(self):
            raise PermissionError('Controlled kill failure')
        def wait(self, timeout=None):
            waiting.set()
            if release.wait(.02):
                self.returncode = 1
                return 1
            raise OSError('Controlled wait failure')
    child = Child()
    def launch(*a, **kw):
        workdirs.append(Path(kw['stderr'].name).parent)
        return child
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'controlled-child')
    monkeypatch.setattr(base, '_ffmpeg_safe_dir', lambda p: p)
    monkeypatch.setattr(base.subprocess, 'Popen', launch)
    def merge():
        try:
            job._mergeMp4Chunks()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    worker = threading.Thread(target=merge)
    try:
        worker.start()
        assert waiting.wait(2)
        job.cancel_download(cleanup=False)
        assert not done.wait(.1), 'A live child was reported as retired'
        assert job._ffmpeg_proc is child and workdirs[0].exists()
        assert not job.wait_stopped(.02)
        release.set()
        worker.join(2)
        assert not worker.is_alive() and not errors
        assert job.wait_stopped(1) and job._ffmpeg_proc is None
        assert not workdirs[0].exists()
        assert not (tmp_path / 'controlled.mp4').exists()
    finally:
        release.set()
        worker.join(3)


def test_real_child_reaped_before_remux_returns(tmp_path, monkeypatch):
    job = bare(tmp_path)
    spawned = threading.Event()
    children, errors = [], []
    real_popen = subprocess.Popen
    def launch(*args, **kwargs):
        child = real_popen([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
        children.append(child)
        spawned.set()
        return child
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'controlled-child')
    monkeypatch.setattr(base, '_ffmpeg_safe_dir', lambda p: p)
    monkeypatch.setattr(base.subprocess, 'Popen', launch)
    def remux():
        try:
            assert job._remux_to_mp4('in.ts', 'out.mp4', str(tmp_path)) is False
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=remux)
    try:
        thread.start()
        assert spawned.wait(2)
        job.cancel_download(cleanup=False)
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert children[0].returncode is not None
        assert job._ffmpeg_proc is None and job.wait_stopped(1)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(3)
        thread.join(3)


def test_collection_cancel_keeps_ownership_when_child_disposal_fails(monkeypatch):
    import io
    from concurrent.futures import CancelledError
    from uav_downloader.core import media_concat
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    errors = []
    class Child:
        returncode = None
        stderr = io.BytesIO(b'')
        def poll(self): return self.returncode
        def terminate(self): raise PermissionError('Controlled terminate failure')
        def kill(self): raise PermissionError('Controlled kill failure')
        def wait(self, timeout=None):
            entered.set()
            if not release.wait(.02):
                raise OSError('Controlled wait failure')
            self.returncode = 1
            return 1
    child = Child()
    monkeypatch.setattr(media_concat.subprocess, 'Popen', lambda *a, **kw: child)
    def run():
        try:
            media_concat._run_ffmpeg(['controlled-child'], cancel_check=lambda: True, timeout=10)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    worker = threading.Thread(target=run)
    try:
        worker.start()
        assert not done.wait(.1), 'Collection released a still-live child'
        assert entered.wait(1)
        release.set()
        worker.join(2)
        assert not worker.is_alive() and child.returncode is not None
        assert len(errors) == 1 and isinstance(errors[0], CancelledError)
    finally:
        release.set()
        worker.join(3)


def test_manager_keeps_slot_and_replacement_until_retirement(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    created = []
    class Job:
        _cancel_job = False
        def is_url_vaildate(self): return True
        def target_name(self): return 'Controlled transfer'
        def start_download(self): return True
        def cancel_download(self, cleanup=True): self._cancel_job = True
        def wait_stopped(self):
            if self is created[0]:
                entered.set()
                assert release.wait(5)
            return True
    def create(*a, **kw):
        job = Job()
        created.append(job)
        return job
    monkeypatch.setattr(browse.M3U8Sites, 'CreateSite', create)
    manager = browse.DownloadManager(subtitle_mode_getter=lambda: 'none', slow_requeue_kbps_getter=lambda: 0)
    url = 'https://jable.tv/videos/retirement/'
    try:
        assert manager.enqueue(url, str(tmp_path))
        assert entered.wait(2)
        assert manager.active_count == 1
        assert manager.restart(url, str(tmp_path))
        assert len(created) == 1
        release.set()
        eventually(lambda: len(created) == 2 and manager.active_count == 0)
        assert manager.wait_stopped(2)
    finally:
        release.set()
        manager.cancel_all(cleanup=False)


@pytest.mark.parametrize('entry', ['headless', 'nogui'])
def test_cli_interrupt_cancels_without_cleanup_and_waits(tmp_path, monkeypatch, entry):
    calls = []
    class Job:
        def is_url_vaildate(self): return True
        def start_download(self): raise KeyboardInterrupt()
        def cancel_download(self, cleanup=True): calls.append(('cancel', cleanup))
        def wait_stopped(self): calls.append(('wait',))
    monkeypatch.setattr(sites, 'create_site', lambda *a, **kw: Job())
    try:
        result = (headless.main(['-o', str(tmp_path), 'https://jable.tv/videos/interrupt/'])
                  if entry == 'headless' else sites.consoles_main('https://jable.tv/videos/interrupt/', str(tmp_path)))
    except KeyboardInterrupt:
        result = 'escaped'
    assert result == 130
    assert calls == [('cancel', False), ('wait',)]


def test_watcher_does_not_remove_cache_outside_owner(tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'segment').write_bytes(b'live cache')
    calls = []
    job = SimpleNamespace(_temp_folder=str(cache), is_target_video_exist=lambda: False,
                          cleanup_temp=lambda: calls.append('owner-request'),
                          wait_stopped=lambda: calls.append('wait'))
    watch.SmallToolWorker._cleanup_temp(SimpleNamespace(_log=lambda *a: None), job)
    assert (cache / 'segment').read_bytes() == b'live cache'
    assert calls == ['owner-request', 'wait']


def test_tk_close_waits_off_ui_then_saves_once_without_changing_old_history(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, callbacks = [], []
    manager = browse.DownloadManager()
    manager.add_item('https://example.test/old', state='已暫停')
    def wait():
        entered.set()
        assert release.wait(5)
        return True
    monkeypatch.setattr(manager, 'wait_stopped', wait)
    monkeypatch.setattr(manager, 'save_csv', lambda path: calls.append('save'))
    app = SimpleNamespace(_startup_complete=True, _dl_drain_id=None,
                          _thumb_executor=SimpleNamespace(shutdown=lambda **kw: None),
                          _dlmgr=manager, after=lambda *args: callbacks.append(args[-1]),
                          destroy=lambda: calls.append('destroy'))
    try:
        browse.ModernApp._on_close(app)
        assert entered.wait(2)
        browse.ModernApp._on_close(app)
        assert calls == [] and len(callbacks) == 1
        assert manager.enqueue('https://example.test/new', 'download') is False
        release.set()
        assert app._close_future.result(2)
        callbacks.pop()()
        assert calls == ['save', 'destroy']
        assert manager.get_items()[0].state == '已暫停'
    finally:
        release.set()


def test_tk_save_failure_preserves_window_and_allows_close_retry(monkeypatch):
    manager = browse.DownloadManager()
    calls = []
    def save(path):
        calls.append('save')
        if calls.count('save') == 1:
            raise OSError('Controlled save failure with synthetic-secret')
    monkeypatch.setattr(manager, 'save_csv', save)
    monkeypatch.setattr(browse.messagebox, 'showerror', lambda title, text: calls.append(text))
    callbacks = []
    app = SimpleNamespace(_startup_complete=True, _dl_drain_id=None,
                          _thumb_executor=SimpleNamespace(shutdown=lambda **kw: None),
                          _dlmgr=manager, after=lambda *a: callbacks.append(a[-1]),
                          destroy=lambda: calls.append('destroy'))
    browse.ModernApp._on_close(app)
    eventually(lambda: app._close_future is None or app._close_future.done())
    if app._close_future is not None:
        browse.ModernApp._poll_close(app, app._close_future)
    assert 'destroy' not in calls and not any('synthetic-secret' in s for s in calls)
    browse.ModernApp._on_close(app)
    future = app._close_future
    assert future.result(2)
    browse.ModernApp._poll_close(app, future)
    assert calls.count('save') == 2 and calls[-1] == 'destroy'


def test_review_repeated_cancel_does_not_clean_a_later_owner(tmp_path):
    job = bare(tmp_path)
    cache = Path(job._temp_folder)
    (cache / 'old').write_bytes(b'old cache')
    job.cancel_download()
    assert job.wait_stopped(2) and not cache.exists()
    cache.mkdir()
    (cache / 'new').write_bytes(b'later owner')
    job.cancel_download()
    assert job.wait_stopped(2)
    assert (cache / 'new').read_bytes() == b'later owner'


def test_review_late_executor_storage_error_preserves_cache(tmp_path):
    job = bare(tmp_path)
    cached = Path(job._temp_folder) / 'segment'
    cached.write_bytes(b'recoverable bytes')
    entered, release = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    def transfer():
        entered.set()
        assert release.wait(5)
        job._storage_error = StorageWriteError(str(cached), OSError(28, 'Controlled disk full'))
    future = pool.submit(transfer)
    job._t2_executor = pool
    try:
        assert entered.wait(2)
        job.cancel_download()
        release.set()
        assert job.wait_stopped(2)
        future.result(1)
        assert cached.read_bytes() == b'recoverable bytes'
    finally:
        release.set()
        pool.shutdown(wait=True)


@pytest.mark.parametrize('callback', [False, True])
def test_review_self_wait_does_not_deadlock_its_owner(tmp_path, callback):
    source = str(Path(base.__file__).resolve().parents[2])
    script = '''
import sys, threading
sys.path.insert(0, sys.argv[1])
from concurrent.futures import ThreadPoolExecutor
from uav_downloader.sites.base import M3U8Crawler
from uav_downloader.core.transfer_lifetime import owned_transfer
j = M3U8Crawler.__new__(M3U8Crawler)
j._cancel_job = False
j._t_executor = j._t2_executor = j._ffmpeg_proc = None
j._cleanup_temp_now = lambda: None
if sys.argv[2] == 'False':
    @owned_transfer
    def run(owner): return owner.wait_stopped()
    print(run(j))
else:
    entered, release, finish = threading.Event(), threading.Event(), threading.Event()
    pool = j._t_executor = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(lambda: release.wait(3))
    def callback(future):
        entered.set()
        assert finish.wait(3)
        print(j.wait_stopped())
    future.add_done_callback(callback)
    release.set()
    assert entered.wait(3)
    j.cancel_download()
    finish.set()
    pool.shutdown(wait=True)
    assert j.wait_stopped(2)
'''
    result = subprocess.run([sys.executable, '-c', script, source, str(callback)],
                            capture_output=True, text=True, encoding='utf-8', timeout=5)
    assert result.returncode == 0, result.stderr
    assert 'False' in result.stdout


def test_review_shutdown_rejects_a_delayed_worker_start(tmp_path, monkeypatch):
    manager = browse.DownloadManager()
    entered, release = threading.Event(), threading.Event()
    called = []
    original = manager._start_download_thread
    def delayed(task):
        entered.set()
        assert release.wait(5)
        original(task)
    monkeypatch.setattr(manager, '_start_download_thread', delayed)
    monkeypatch.setattr(manager, '_run_download', lambda task: called.append(task.url))
    saved = []
    monkeypatch.setattr(manager, 'save_csv', lambda path: saved.append(manager.get_items()[0].state))
    app = SimpleNamespace(_startup_complete=True, _dl_drain_id=None,
                          _thumb_executor=SimpleNamespace(shutdown=lambda **kw: None),
                          _dlmgr=manager, after=lambda *a: None, destroy=lambda: None)
    starter = threading.Thread(target=lambda: manager.enqueue('https://example.test/late', str(tmp_path)))
    try:
        starter.start()
        assert entered.wait(2)
        browse.ModernApp._on_close(app)
        assert app._close_future.result(2)
        assert saved == ['未完成'], 'The admitted row must remain resumable before a delayed starter returns'
        release.set()
        starter.join(2)
        assert manager.wait_stopped(1)
        assert not called
    finally:
        release.set()
        starter.join(3)


def test_review_queue_lock_remains_available_during_close_save(monkeypatch):
    manager = browse.DownloadManager()
    entered, release = threading.Event(), threading.Event()
    def save(path):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(manager, 'save_csv', save)
    app = SimpleNamespace(_startup_complete=True, _dl_drain_id=None,
                          _thumb_executor=SimpleNamespace(shutdown=lambda **kw: None),
                          _dlmgr=manager, after=lambda *a: None, destroy=lambda: None)
    try:
        browse.ModernApp._on_close(app)
        assert entered.wait(2)
        acquired = manager._lock.acquire(blocking=False)
        if acquired:
            manager._lock.release()
        assert acquired, 'Close/save held the queue lock across blocked filesystem I/O'
    finally:
        release.set()
        assert app._close_future.result(2)


def test_review_watcher_repeated_close_schedules_one_shutdown(monkeypatch):
    calls, callbacks = [], []
    app = SimpleNamespace(_build_gen=0, _cfg={},
                          _cancel_category_work=lambda: calls.append('category-stop'),
                          _get_selected_targets=lambda: [],
                          _worker=SimpleNamespace(stop=lambda: calls.append('stop'),
                              cancel_active_download=lambda: calls.append('cancel'),
                              wait_until_stopped=lambda timeout: False),
                          after=lambda *a: callbacks.append(a[-1]))
    app._poll_worker_close = lambda: watch.SmallToolApp._poll_worker_close(app)
    monkeypatch.setattr(watch, 'update_config', lambda *a, **kw: calls.append('save'))
    watch.SmallToolApp._on_close(app)
    watch.SmallToolApp._on_close(app)
    assert calls.count('stop') == calls.count('cancel') == calls.count('save') == 1
    assert len(callbacks) == 1


def test_review_wait_includes_cleanup_requested_during_executor_join(tmp_path, monkeypatch):
    job = bare(tmp_path)
    cache = Path(job._temp_folder)
    (cache / 'segment').write_bytes(b'owned cache')
    worker_entered, release_worker, joining = [threading.Event() for _ in range(3)]
    cleanup_entered, release_cleanup, finished = [threading.Event() for _ in range(3)]
    pool = ThreadPoolExecutor(max_workers=1)
    pool.submit(lambda: (worker_entered.set(), release_worker.wait(5)))
    job._t2_executor = pool
    real_shutdown, real_cleanup = pool.shutdown, job._cleanup_temp_now
    def shutdown(**kwargs):
        joining.set()
        real_shutdown(**kwargs)
    def cleanup():
        cleanup_entered.set()
        assert release_cleanup.wait(5)
        real_cleanup()
    monkeypatch.setattr(pool, 'shutdown', shutdown)
    monkeypatch.setattr(job, '_cleanup_temp_now', cleanup)
    results = []
    def wait():
        results.append(job.wait_stopped())
        finished.set()
    waiter = threading.Thread(target=wait)
    try:
        assert worker_entered.wait(2)
        waiter.start()
        assert joining.wait(2)
        job.cancel_download()
        release_worker.set()
        assert cleanup_entered.wait(2)
        assert not finished.wait(.05), 'Unbounded wait returned before deferred cleanup'
        release_cleanup.set()
        waiter.join(2)
        assert results == [True] and not cache.exists()
    finally:
        release_worker.set()
        release_cleanup.set()
        real_shutdown(wait=True)
        waiter.join(3)
