"""Controlled stop requests at real download I/O and admission boundaries."""
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from uav_downloader.apps.browse import DownloadManager
from uav_downloader.sites import base,missav,supjav
from uav_downloader.core.stop import StopContext,StopReason,DownloadStopped,current_stop_context,stop_scope


def test_cancel_latch_cannot_be_cleared_by_a_late_start_reset():
    job=base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job=False
    job._cancel_job=True
    job._cancel_job=False  # A late startup assignment must never revoke stop.
    assert job._cancel_job is True


def segment_job(tmp_path,monkeypatch):
    job=base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job=False;job._storage_error=None;job._temp_folder=str(tmp_path)
    job._key_content=None;job._m3u8_headers=lambda:{};job._transform_segment=lambda b:b
    job._speed_lock=threading.Lock();job._pending_set={(0,'https://example.test/0.ts')}
    job._speed_start=time.time();job._bytes_downloaded=0;job._job_total=1;job._progress_callback=None
    job._tsList=['https://example.test/0.ts']
    monkeypatch.setattr(base,'_http_get',lambda *a,**kw:SimpleNamespace(status_code=200,content=b'\x47'+bytes(187),headers={}))
    return job


def test_stop_during_segment_write_closes_part_without_publishing(tmp_path,monkeypatch):
    job=segment_job(tmp_path,monkeypatch)
    real_open=base.storage_open
    closed=[]
    class Writer:
        def __enter__(self):
            self.stream=real_open(str(tmp_path/'000000.mp4.part'),'wb')
            self.stream.__enter__();return self
        def write(self,data):
            result=self.stream.write(data)
            job._cancel_job=True
            return result
        def __exit__(self,*args):
            self.stream.__exit__(*args);closed.append(True)
    monkeypatch.setattr(base,'storage_open',lambda *a,**kw:Writer())
    assert job._scrape((0,'https://example.test/0.ts')) is False
    assert closed and not (tmp_path/'000000.mp4').exists()
    assert job._bytes_downloaded==0 and job._pending_set


def test_already_stopped_same_volume_move_preserves_source(tmp_path):
    job=base.M3U8Crawler.__new__(base.M3U8Crawler);job._cancel_job=True
    src,dst=tmp_path/'source.ts',tmp_path/'out.mp4.part'
    src.write_bytes(b'valuable data')
    assert job._cancellable_move(str(src),str(dst)) is False
    assert src.read_bytes()==b'valuable data' and not dst.exists()


def test_stopped_remux_does_not_launch_new_subprocess(tmp_path,monkeypatch):
    job=base.M3U8Crawler.__new__(base.M3U8Crawler);job._cancel_job=True
    launches=[]
    class Process:
        returncode=0
        def wait(self,**kwargs):return 0
    monkeypatch.setattr(base,'locate_ffmpeg',lambda:'synthetic-ffmpeg')
    monkeypatch.setattr(base.subprocess,'Popen',lambda *a,**kw:launches.append(a) or Process())
    assert job._remux_to_mp4(str(tmp_path/'source.ts'),str(tmp_path/'out.mp4'),str(tmp_path)) is False
    assert launches==[]


def test_cancelled_worker_exits_prepare_semaphore_without_waiting_for_owner(tmp_path,monkeypatch):
    manager=DownloadManager(subtitle_mode_getter=lambda:'none',slow_requeue_kbps_getter=lambda:0)
    manager._prep_sem.acquire()
    entered=threading.Event();real_acquire=manager._prep_sem.acquire
    def acquire(*a,**kw):entered.set();return real_acquire(*a,**kw)
    monkeypatch.setattr(manager._prep_sem,'acquire',acquire)
    workers=[];created=[]
    from uav_downloader.apps import browse as task_queue
    monkeypatch.setattr(task_queue.M3U8Sites,'CreateSite',lambda *a,**kw:created.append(a))
    def start(task):
        worker=threading.Thread(target=manager._run_download,args=(task,));workers.append(worker);worker.start()
    monkeypatch.setattr(manager,'_start_download_thread',start)
    url='https://jable.tv/videos/stopping-001/'
    try:
        manager.add_item(url);manager.enqueue(url,str(tmp_path));assert entered.wait(2)
        manager.cancel_all(cleanup=False)
        workers[0].join(1)
        assert not workers[0].is_alive() and not created and manager.active_count==0
    finally:
        manager._prep_sem.release()
        for worker in workers:worker.join(3)


def test_cancelled_missav_worker_leaves_site_gate_before_owner_releases(monkeypatch):
    gate=threading.BoundedSemaphore(1);gate.acquire()
    entered=threading.Event()
    monkeypatch.setattr(missav.SiteMissAV,'_segment_request_gate',gate)
    invoked=[]
    monkeypatch.setattr(base.M3U8Crawler,'_scrape',lambda *a:invoked.append(True) or True)
    job=missav.SiteMissAV.__new__(missav.SiteMissAV);job._cancel_job=False
    results=[]
    def run():
        entered.set();results.append(job._scrape((0,'https://example.test/0.ts')))
    worker=threading.Thread(target=run)
    try:
        worker.start();assert entered.wait(2)
        job._cancel_job=True;worker.join(1)
        assert not worker.is_alive() and results==[False] and not invoked
    finally:
        gate.release();worker.join(3)


def test_storage_stop_overrides_destructive_cleanup_intent():
    context=StopContext()
    context.request(StopReason.CANCEL,cleanup=True)
    assert context.cleanup_requested
    context.request(StopReason.STORAGE_ERROR)
    context.request(StopReason.CANCEL,cleanup=True)
    assert context.reason==StopReason.STORAGE_ERROR and not context.cleanup_requested
    assert not context.finished.is_set(), 'request is not acknowledgement of worker exit'


def test_storage_stopped_job_does_not_clean_even_if_old_caller_requests_cleanup():
    job=base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._t_executor=job._t2_executor=None
    calls=[];job.cleanup_temp=lambda:calls.append(True)
    job.request_stop(StopReason.STORAGE_ERROR)
    job.cancel_download(cleanup=True)
    assert not calls and job._stop().reason==StopReason.STORAGE_ERROR


def test_stop_scope_is_restored_and_constructor_inherits_stop_before_parsing(tmp_path):
    context=StopContext();context.request(StopReason.SHUTDOWN)
    parsed=[]
    class Site(base.M3U8Crawler):
        website_dirname_pattern=r'https://example.test/([^/]+)/'
        def get_url_infos(self):parsed.append(True)
    with stop_scope(context):
        assert current_stop_context() is context
        job=Site('https://example.test/a-001/',savepath=str(tmp_path),silence=True)
        assert job._stop() is context
    assert current_stop_context() is None and not parsed and job._cancel_job
    assert isinstance(job._last_error,DownloadStopped)


def test_explicit_cancel_after_pause_replaces_pause_intent(tmp_path):
    manager=DownloadManager();started=[];manager._start_download_thread=started.append
    url='https://jable.tv/videos/stopping-002/'
    manager.add_item(url);manager.enqueue(url,str(tmp_path));task=started[0]
    assert manager.pause(url) and task.stop.reason==StopReason.PAUSE
    manager.cancel_all()
    assert task.stop.reason==StopReason.CANCEL and not task.pause_requested
    manager._complete_download(task,'已取消')
    assert manager.get_items()[0].state=='已取消'


def test_hls_backoff_wakes_on_stop_without_another_request(tmp_path,monkeypatch):
    job=segment_job(tmp_path,monkeypatch)
    job._max_workers=1;job._segment_retry_rounds=3
    job._segment_retry_base_delay=job._segment_retry_max_delay=600
    calls=[];job._scrape=lambda task:calls.append(task) or False
    entered=threading.Event();real_wait=job._stop().wait
    def wait(*args,**kwargs):entered.set();return real_wait(*args,**kwargs)
    monkeypatch.setattr(job._stop(),'wait',wait)
    worker=threading.Thread(target=job._startCrawl)
    try:
        worker.start();assert entered.wait(2)
        job.request_stop(StopReason.SHUTDOWN)
        worker.join(1)
        assert not worker.is_alive() and len(calls)==1
    finally:
        job._cancel_job=True;worker.join(3)


def test_cancel_during_cffi_failure_never_starts_requests_fallback(monkeypatch):
    context=StopContext();fallback=[]
    def get(*a,**kw):
        context.request();raise ConnectionError('synthetic interrupted transport')
    monkeypatch.setattr(base,'_use_cffi',True)
    monkeypatch.setattr(base,'_get_cffi_session',lambda:SimpleNamespace(get=get))
    monkeypatch.setattr(base,'_get_session',lambda:SimpleNamespace(get=lambda *a,**kw:fallback.append(True)))
    with stop_scope(context),pytest.raises(DownloadStopped):
        base._http_get('https://example.test/media')
    assert not fallback


def test_late_http_response_is_closed_and_not_returned_after_stop(monkeypatch):
    context=StopContext();closed=[]
    def get(*a,**kw):
        context.request()
        return SimpleNamespace(close=lambda:closed.append(True))
    monkeypatch.setattr(base,'_use_cffi',False)
    monkeypatch.setattr(base,'_get_session',lambda:SimpleNamespace(get=get))
    with stop_scope(context),pytest.raises(DownloadStopped):
        base._http_get('https://example.test/media')
    assert closed==[True]


def test_cross_device_copy_drops_data_read_after_stop(tmp_path,monkeypatch):
    import errno
    job=base.M3U8Crawler.__new__(base.M3U8Crawler);job._cancel_job=False
    src,dst=tmp_path/'source.ts',tmp_path/'part.mp4'
    src.write_bytes(b'valuable source')
    writes=[]
    class Reader:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,size):job._cancel_job=True;return b'late read data'
    class Writer(Reader):
        def write(self,data):writes.append(data);return len(data)
    def cross_volume(*a):raise OSError(errno.EXDEV,'synthetic cross-device move')
    monkeypatch.setattr(base.os,'replace',cross_volume)
    monkeypatch.setattr(base,'storage_open',lambda path,mode:Reader() if mode=='rb' else Writer())
    assert job._cancellable_move(str(src),str(dst)) is False
    assert not writes and src.read_bytes()==b'valuable source'


def test_merge_copy_observes_stop_between_reads_and_closes_before_cleanup(tmp_path,monkeypatch):
    job=segment_job(tmp_path,monkeypatch)
    job._dest_folder=str(tmp_path);job._targetName='output'
    segment=Path(job._seg_savename(0));segment.write_bytes(b'valuable cache')
    events=[];writes=[]
    class Reader:
        def __init__(self):self.reads=0
        def __enter__(self):return self
        def __exit__(self,*a):events.append('reader closed')
        def read(self,size):
            self.reads+=1
            if self.reads>1:return b''
            job._cancel_job=True;return b'late read data'
    class Writer(Reader):
        def __exit__(self,*a):events.append('writer closed')
        def write(self,data):writes.append(data);return len(data)
    real_cleanup=base.shutil.rmtree
    def cleanup(*a,**kw):events.append('cleanup');return real_cleanup(*a,**kw)
    monkeypatch.setattr(base,'storage_open',lambda path,mode:Reader() if mode=='rb' else Writer())
    monkeypatch.setattr(base.shutil,'rmtree',cleanup)
    assert job._mergeMp4Chunks()==0
    assert not writes and events==['reader closed','writer closed','cleanup']
    assert segment.read_bytes()==b'valuable cache' and not (tmp_path/'output.mp4').exists()


def test_real_segment_worker_propagates_stop_to_transport_fallback(tmp_path,monkeypatch):
    real_get=base._http_get
    job=segment_job(tmp_path,monkeypatch)
    # Use the production helper rather than segment_job's fake high-level GET.
    monkeypatch.setattr(base,'_http_get',real_get)
    fallback=[]
    def get(*a,**kw):job._cancel_job=True;raise ConnectionError('synthetic interrupted transfer')
    monkeypatch.setattr(base,'_use_cffi',True)
    monkeypatch.setattr(base,'_get_cffi_session',lambda:SimpleNamespace(get=get))
    monkeypatch.setattr(base,'_get_session',lambda:SimpleNamespace(get=lambda *a,**kw:fallback.append(True)))
    assert job._scrape((0,'https://example.test/0.ts')) is False
    assert not fallback and not (tmp_path/'000000.mp4').exists()


def test_direct_stop_during_probe_does_not_bind_cache_or_create_output(tmp_path):
    job=supjav.SiteSupJav.__new__(supjav.SiteSupJav);job._cancel_job=False
    writes=[]
    job._storage=SimpleNamespace(session=lambda:nullcontext(),cache=tmp_path,
        bind_media=lambda desc:writes.append('cache'),output_exists=lambda:False)
    job._reserve_storage_output=lambda:writes.append('output')
    job._download_direct_owned=lambda:writes.append('download') or False
    def probe():job._cancel_job=True;return dict(kind='direct')
    job._probe_direct_storage_identity=probe
    assert job._download_direct() is False
    assert not writes
