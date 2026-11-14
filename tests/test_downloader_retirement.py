"""Downloader-owned work acknowledges exit only after its I/O is disposed."""
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from uav_downloader.core.stop import execution_scope
from uav_downloader.sites import base, supjav


def bare_job(kind=base.M3U8Crawler):
    job = kind.__new__(kind)
    job._cancel_job = False
    job._t_executor = job._t2_executor = job._t_future = None
    return job


@pytest.mark.parametrize('direct', [False, True])
def test_standalone_transfer_keeps_exit_unacknowledged_until_io_returns(monkeypatch, direct):
    job = bare_job(supjav.SiteSupJav if direct else base.M3U8Crawler)
    entered, release = threading.Event(), threading.Event()
    results = []
    def transfer():
        entered.set()
        assert release.wait(3)
        return False
    if direct:
        monkeypatch.setattr(job, '_download_direct_owned', transfer)
        target = job._download_direct
    else:
        monkeypatch.setattr(job, '_start_download_scoped', transfer)
        target = job.start_download
    worker = threading.Thread(target=lambda: results.append(target()))
    try:
        worker.start()
        assert entered.wait(2)
        assert job._stop().active_count > 0
        job.request_stop()
        assert not job._stop().finished.is_set()
        release.set()
        worker.join(2)
        assert not worker.is_alive() and results == [False]
        assert job._stop().finished.is_set()
    finally:
        release.set()
        worker.join(3)


def test_provider_fallback_does_not_acknowledge_exit_between_sources(monkeypatch):
    job = bare_job(supjav.SiteSupJav)
    job._hls_sources = [('first', 'https://example.test/1', {}),
                        ('second', 'https://example.test/2', {})]
    job.cleanup_temp = lambda: None
    seen = []
    def transfer():
        seen.append((job._m3u8url, job._stop().finished.is_set()))
        if len(seen) == 1:
            raise OSError('controlled first source failure')
        return True
    monkeypatch.setattr(job, '_start_download_scoped', transfer)
    assert job.start_download() is True
    assert [finished for _, finished in seen] == [False, False]
    assert job._stop().finished.is_set()


def test_cancel_retains_executor_handles_until_workers_are_joined():
    job = bare_job()
    entered, release = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    def transfer():
        with execution_scope(job._stop()):
            entered.set()
            assert release.wait(3)
    future = pool.submit(transfer)
    job._t_executor = pool
    try:
        assert entered.wait(2)
        job.cancel_download(cleanup=False)
        assert job._t_executor is pool
        assert job.wait_stopped(timeout=0.03) is False
        release.set()
        assert job.wait_stopped(timeout=2) is True
        assert future.done() and not any(t.is_alive() for t in pool._threads)
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_background_transfer_reserves_ownership_before_thread_start(monkeypatch):
    job = bare_job()
    entered, release = threading.Event(), threading.Event()
    def transfer():
        entered.set()
        assert release.wait(3)
        return False
    monkeypatch.setattr(job, '_start_download_scoped', transfer)
    try:
        job.begin_concurrent_download()
        assert entered.wait(2)
        job.cancel_download(cleanup=False)
        assert job.wait_stopped(timeout=0.03) is False
        release.set()
        assert job.wait_stopped(timeout=2) is True
        assert job._stop().finished.is_set()
        assert job.is_concurrent_dowload_completed()
    finally:
        release.set()
        if getattr(job, 'wait_stopped', None):
            job.wait_stopped(timeout=3)
        elif job._t_future:
            job._t_future.result(3)


def test_stop_before_background_body_enters_still_waits_for_reserved_worker(monkeypatch):
    job = bare_job()
    parked, release = threading.Event(), threading.Event()
    original = job.start_download
    calls = []
    def delayed_start():
        parked.set()
        assert release.wait(3)
        return original()
    monkeypatch.setattr(job, 'start_download', delayed_start)
    monkeypatch.setattr(job, '_start_download_scoped', lambda: calls.append(True))
    try:
        job.begin_concurrent_download()
        assert parked.wait(2)
        job.cancel_download(cleanup=False)
        assert job._stop().active_count == 1
        assert not job.wait_stopped(timeout=0.03)
        release.set()
        assert job.wait_stopped(timeout=2)
        assert calls == [] and job._t_future.result() is False
    finally:
        release.set()
        job.wait_stopped(timeout=3)


def test_completed_poll_waits_for_the_owned_executor_thread(monkeypatch):
    job = bare_job()
    entered, release, callback_entered, callback_release = [threading.Event() for _ in range(4)]
    def transfer():
        entered.set()
        assert release.wait(3)
        return True
    def callback(future):
        callback_entered.set()
        assert callback_release.wait(3)
    monkeypatch.setattr(job, '_start_download_scoped', transfer)
    try:
        job.begin_concurrent_download()
        assert entered.wait(2)
        job._t_future.add_done_callback(callback)
        release.set()
        assert callback_entered.wait(2)
        assert not job.is_concurrent_dowload_completed()
        callback_release.set()
        assert job.wait_stopped(timeout=2)
        assert job.is_concurrent_dowload_completed()
    finally:
        release.set()
        callback_release.set()
        job.wait_stopped(timeout=3)


def test_stop_while_locating_ffmpeg_does_not_launch_a_child(tmp_path, monkeypatch):
    job = bare_job()
    launches = []
    def locate():
        job.request_stop()
        return 'controlled-child'
    monkeypatch.setattr(base, 'locate_ffmpeg', locate)
    monkeypatch.setattr(base.subprocess, 'Popen', lambda *a, **kw: launches.append(True))
    assert job._remux_to_mp4(str(tmp_path/'in.ts'), str(tmp_path/'out.mp4'), str(tmp_path)) is False
    assert launches == [] and job._stop().finished.is_set()


def test_cancelled_queued_future_releases_its_reservation_but_joins_executor(monkeypatch):
    job = bare_job()
    entered, release = threading.Event(), threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    def occupying_worker():
        entered.set()
        assert release.wait(3)
    occupying = executor.submit(occupying_worker)
    monkeypatch.setattr(base.concurrent.futures, 'ThreadPoolExecutor', lambda **kw: executor)
    try:
        assert entered.wait(2)
        job.begin_concurrent_download()
        job.cancel_download(cleanup=False)
        assert job._t_future.cancelled() and job._stop().active_count == 0
        assert not job.wait_stopped(timeout=0.03)
        release.set()
        assert job.wait_stopped(timeout=2) and occupying.done()
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_stopped_merge_preserves_existing_partial_without_creating_workdir(tmp_path):
    job = bare_job()
    job._dest_folder = job._temp_folder = str(tmp_path)
    job._targetName = 'controlled'
    job._tsList = ['https://example.test/0.ts']
    partial = tmp_path/'controlled.mp4.part'
    partial.write_bytes(b'recoverable data')
    job.request_stop()
    assert job._mergeMp4Chunks() == 0
    assert partial.read_bytes() == b'recoverable data'
    assert not list(tmp_path.glob('uav-remux-*'))


def test_real_child_is_reaped_before_standalone_remux_acknowledges_exit(tmp_path, monkeypatch):
    job = bare_job()
    real_popen = subprocess.Popen
    spawned, finished = threading.Event(), threading.Event()
    children, errors, results = [], [], []
    marker = tmp_path / 'child-ready'
    def launch(*args, **kwargs):
        child = real_popen([sys.executable, '-c',
            'import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text("ready"); time.sleep(30)',
            str(marker)], **kwargs)
        children.append(child)
        spawned.set()
        return child
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'controlled-child')
    monkeypatch.setattr(base, '_ffmpeg_safe_dir', lambda p: p)
    monkeypatch.setattr(base.subprocess, 'Popen', launch)
    def remux():
        try:
            results.append(job._remux_to_mp4(str(tmp_path/'in.ts'), str(tmp_path/'out.mp4'), str(tmp_path)))
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()
    worker = threading.Thread(target=remux)
    try:
        worker.start()
        assert spawned.wait(2)
        job.cancel_download(cleanup=False)
        assert finished.wait(3)
        worker.join(2)
        assert not errors and results == [False]
        assert children[0].returncode is not None
        assert job._ffmpeg_proc is None and job._stop().finished.is_set()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
        worker.join(3)


def test_failed_kill_and_wait_keep_workdir_and_ownership_until_child_exits(tmp_path, monkeypatch):
    job = bare_job()
    job._dest_folder = str(tmp_path)
    job._targetName = 'controlled'
    job._tsList = ['https://example.test/0.ts']
    job._temp_folder = str(tmp_path / 'segments')
    (tmp_path/'segments').mkdir()
    (tmp_path/'segments'/'000000.mp4').write_bytes(b'controlled media')
    waiting, release, ended = threading.Event(), threading.Event(), threading.Event()
    errors, workdirs = [], []
    class Process:
        returncode = None
        def kill(self):
            raise PermissionError('controlled kill failure')
        def wait(self, timeout=None):
            waiting.set()
            if release.wait(0.02):
                self.returncode = 1
                return 1
            raise OSError('controlled wait failure')
    process = Process()
    def launch(*args, **kwargs):
        workdirs.append(kwargs['stderr'].name)
        return process
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'controlled-child')
    monkeypatch.setattr(base, '_ffmpeg_safe_dir', lambda p: p)
    monkeypatch.setattr(base.subprocess, 'Popen', launch)
    def merge():
        try:
            with execution_scope(job._stop()):
                job._mergeMp4Chunks()
        except BaseException as exc:
            errors.append(exc)
        finally:
            ended.set()
    worker = threading.Thread(target=merge)
    try:
        worker.start()
        assert waiting.wait(2)
        job.cancel_download(cleanup=False)
        assert not ended.wait(0.1), 'a live child must keep its owner and work directory'
        assert job._ffmpeg_proc is process and not job._stop().finished.is_set()
        assert Path(workdirs[0]).parent.exists()
        release.set()
        worker.join(2)
        assert not worker.is_alive() and not errors
        assert job._ffmpeg_proc is None and job._stop().finished.is_set()
        assert not Path(workdirs[0]).parent.exists()
        assert not (tmp_path/'controlled.mp4').exists()
    finally:
        release.set()
        worker.join(3)


def test_task_service_keeps_slot_until_an_unreaped_child_really_exits(tmp_path, monkeypatch):
    from uav_downloader.apps import browse as task_queue
    from uav_downloader.apps.browse import DownloadManager
    waiting, release = threading.Event(), threading.Event()
    jobs = []
    class Process:
        returncode = None
        def kill(self):
            raise PermissionError('controlled kill failure')
        def wait(self, timeout=None):
            waiting.set()
            if not release.wait(0.02):
                raise subprocess.TimeoutExpired('controlled-child', timeout)
            self.returncode = 1
            return 1
    process = Process()
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'controlled-child')
    monkeypatch.setattr(base, '_ffmpeg_safe_dir', lambda p: p)
    monkeypatch.setattr(base.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(task_queue.config, 'get_minimum_free_gb', lambda: 0)
    def create(*args, **kwargs):
        job = bare_job()
        job._url = args[0]
        job._progress_unit = 'bytes'
        job._source_subtitle_evidence = ()
        job.is_url_vaildate = lambda: True
        job.target_name = lambda: 'Controlled child'
        job._start_download_scoped = lambda: job._remux_to_mp4(
            str(tmp_path/'in.ts'), str(tmp_path/'out.mp4'), str(tmp_path))
        jobs.append(job)
        return job
    monkeypatch.setattr(task_queue.M3U8Sites, 'CreateSite', create)
    manager = DownloadManager(subtitle_mode_getter=lambda: 'none',
                              slow_requeue_kbps_getter=lambda: 0,
                              auto_retry_count_getter=lambda: 0)
    try:
        manager.enqueue('https://jable.tv/videos/stop-child/', str(tmp_path))
        assert waiting.wait(2)
        manager.cancel_all(cleanup=False)
        assert not manager.wait_stopped(timeout=0.05)
        assert manager.active_count == 1
        assert jobs[0]._ffmpeg_proc is process and not jobs[0]._stop().finished.is_set()
        release.set()
        assert manager.wait_stopped(timeout=2)
        assert manager.active_count == 0 and jobs[0]._ffmpeg_proc is None
        assert manager.wait_stopped(timeout=1)
    finally:
        release.set()
        manager.wait_stopped(timeout=3)
