"""Compatibility UI producers must use the same atomic boundary as formal Qt."""
from concurrent.futures import Future
from types import SimpleNamespace
import threading
import time

import pytest

from uav_downloader.apps import browse
from uav_downloader.apps.browse import DownloadManager


def app_fixture(monkeypatch):
    monkeypatch.setattr(browse.messagebox, 'showinfo', lambda *a, **k: None)
    manager = DownloadManager(max_concurrent=2)
    started, messages, callbacks = [], [], []
    manager._start_download_thread = lambda task: started.append(task.url)
    app = SimpleNamespace(_is_closing=False, _build_gen=1, _dlmgr=manager,
        _listing_lock=threading.Lock(), _listing_requests=set(), _listing_status_request=None,
        after=lambda *a: None,
        _ui=lambda callback, **kw: callbacks.append(callback),
        _status_lbl=SimpleNamespace(configure=lambda **kw: messages.append(kw['text'])))
    monkeypatch.setattr(browse.MissAVBrowser, 'page_url', lambda url,page: str(page))
    monkeypatch.setattr(browse.MissAVBrowser, 'fetch_page', lambda page:
        [dict(url='https://missav.ai/en/abc-1',title='A')] if page=='1' else [])
    monkeypatch.setattr(browse.time, 'sleep', lambda seconds: None)
    return app,started,messages,callbacks


def test_compat_listing_cancel_between_validation_and_add_cannot_resurrect(monkeypatch):
    app,started,_,_ = app_fixture(monkeypatch)
    real = browse.M3U8Sites.VaildateUrl
    first = True
    def valid(url):
        nonlocal first
        if first:
            first = False
            app._dlmgr.clear_all()
        return real(url)
    monkeypatch.setattr(browse.M3U8Sites, 'VaildateUrl', valid)
    browse.ModernApp._crawl_listing(app,'https://missav.ai/en/new','download')
    assert app._dlmgr.get_items() == [] and started == []


def test_new_epoch_enqueue_during_clear_cleanup_survives_atomic_clear(tmp_path, monkeypatch):
    manager = DownloadManager()
    manager.add_item('https://jable.tv/videos/old-001/')
    manager._start_download_thread = lambda task: None
    entered,release = threading.Event(),threading.Event()
    errors=[]
    def cleanup(contexts,cleanup=True):
        entered.set(); assert release.wait(3)
    monkeypatch.setattr(manager,'_cancel_contexts',cleanup)
    def clear():
        try:
            manager.clear_all()
        except Exception as error:
            errors.append(error)
    worker=threading.Thread(target=clear)
    try:
        worker.start(); assert entered.wait(2)
        url='https://jable.tv/videos/new-001/'
        assert manager.enqueue_listing_video(dict(url=url),str(tmp_path),manager.cancellation_epoch)
        release.set(); worker.join(3)
        assert not worker.is_alive() and not errors
        assert [item.url for item in manager.get_items()] == [url]
    finally:
        release.set(); worker.join(4)


def test_queued_compat_status_callbacks_cannot_override_cancel_or_new_work(monkeypatch):
    app,_,messages,callbacks=app_fixture(monkeypatch)
    browse.ModernApp._crawl_listing(app,'https://missav.ai/en/new','download')
    assert callbacks
    app._dlmgr.cancel_all()
    for callback in callbacks:
        callback()
    assert messages == []


def test_compat_worker_starting_after_cancel_does_not_adopt_new_epoch(monkeypatch):
    app,started,_,_=app_fixture(monkeypatch)
    calls,workers=[],[]
    monkeypatch.setattr(browse.MissAVBrowser,'fetch_page',lambda page: calls.append(page) or [dict(url='https://missav.ai/en/abc-1')])
    class Thread:
        def __init__(self,target,args=(),**kw):
            self.target,self.args=target,args
        def start(self):
            workers.append(self)
    monkeypatch.setattr(browse.threading,'Thread',Thread)
    text=['https://missav.ai/en/new']
    app._dl_url_var=SimpleNamespace(get=lambda:text[0],set=lambda value:text.__setitem__(0,value))
    app._dest_var=SimpleNamespace(get=lambda:'download')
    app._preflight_download_destination=lambda:True
    app._is_listing_url=lambda url:True
    app._crawl_listing=lambda *args:browse.ModernApp._crawl_listing(app,*args)
    browse.ModernApp._download_url(app)
    browse.ModernApp._cancel_all(app)
    assert len(workers)==1
    workers[0].target(*workers[0].args)
    assert calls==[] and started==[] and app._dlmgr.get_items()==[]


@pytest.mark.parametrize('operation',['_cancel_all','_clear_queue','_clear_saved_queue','_on_close'])
def test_compat_controls_stop_registered_producer_and_drop_late_callbacks(monkeypatch,operation):
    app,started,messages,callbacks=app_fixture(monkeypatch)
    request=browse.ModernApp._register_listing(app,'https://missav.ai/en/new','download')
    monkeypatch.setattr(browse.messagebox,'askyesno',lambda *a,**kw:True)
    app._dl_gen=0
    app._save_queue_async=lambda **kw:None
    app._refresh_downloads=lambda **kw:None
    app.withdraw=lambda:None
    app._dl_drain_id=None
    app._thumb_executor=SimpleNamespace(shutdown=lambda **kw:None)
    saved=Future();saved.set_result(True)
    app._queue_writer=SimpleNamespace(submit=lambda snapshot:saved)
    app._poll_close_save=lambda *args:None
    getattr(browse.ModernApp,operation)(app)
    assert request.stopped.is_set()
    browse.ModernApp._crawl_listing(app,request.url,request.destination,request=request)
    for callback in callbacks:
        callback()
    assert not started and not app._dlmgr.get_items()
    assert not any('Crawling' in str(m) for m in messages)
    assert request.done.result(0)==0 and not app._listing_requests


def test_atomic_listing_preserves_batch_options_and_rejects_stopped_producer(tmp_path,monkeypatch):
    manager=DownloadManager()
    monkeypatch.setattr(manager,'_arm_schedule_locked',lambda:None)
    video=dict(url='https://jable.tv/videos/a-001/',title='Title',thumbnail='https://example.test/a.jpg',_site='JableTV')
    due=time.time()+600
    epoch=manager.cancellation_epoch
    assert not manager.enqueue_listing_video(video,str(tmp_path),epoch,cancel_check=lambda:True)
    assert manager.enqueue_listing_video(video,str(tmp_path),epoch,resolution='720',filename_template='{code}',not_before=due)
    item=manager.get_items()[0]
    assert (item.resolution,item.filename_template,item.thumbnail,item.site,item.not_before)==('720','{code}',video['thumbnail'],'JableTV',due)


def test_new_compat_producer_after_clear_has_independent_context(monkeypatch):
    app,started,messages,callbacks=app_fixture(monkeypatch)
    old=browse.ModernApp._register_listing(app,'https://missav.ai/en/new','download')
    browse.ModernApp._stop_listing_requests(app)
    app._dlmgr.clear_all()
    browse.ModernApp._crawl_listing(app,old.url,old.destination,request=old)
    browse.ModernApp._crawl_listing(app,'https://missav.ai/en/new','download')
    for callback in callbacks:
        callback()
    assert started==['https://missav.ai/en/abc-1']
    assert messages and not app._listing_requests


@pytest.mark.parametrize('phase',['video','subtitle'])
def test_same_url_new_generation_waits_for_old_instance_exit(tmp_path,phase):
    manager=DownloadManager(max_concurrent=2)
    started=[]
    manager._start_download_thread=started.append
    url='https://jable.tv/videos/handoff-001/'
    manager.add_item(url,name='Old')
    manager.enqueue(url,str(tmp_path))
    old=started[0]
    if phase=='subtitle':
        manager._active.pop(url)
        manager._subtitle_active[url]=old
    manager.clear_all()
    assert manager.enqueue_listing_video(dict(url=url,title='New'),str(tmp_path),manager.cancellation_epoch)
    assert manager.pending_count==1 and started==[old]
    manager._try_next()
    assert started==[old], 'capacity must not overwrite the still-running old URL instance'
    complete=manager._complete_download if phase=='video' else manager._complete_subtitle
    complete(old,'已取消',error='old completion')
    assert len(started)==2 and started[1] is not old
    assert manager._active[url] is started[1] and manager.get_items()[0].name=='New'
    assert not manager.get_items()[0].error
    complete(old,'未完成',error='late old error')
    assert manager._active[url] is started[1] and not manager.get_items()[0].error


def test_new_same_url_waits_for_both_old_worker_and_slow_clear_cleanup(tmp_path,monkeypatch):
    manager=DownloadManager(max_concurrent=2)
    started=[]
    manager._start_download_thread=started.append
    url='https://jable.tv/videos/handoff-002/'
    manager.add_item(url);manager.enqueue(url,str(tmp_path));old=started[0]
    entered,release=threading.Event(),threading.Event()
    errors=[]
    def cleanup(contexts,cleanup=True):
        entered.set();assert release.wait(3)
    monkeypatch.setattr(manager,'_cancel_contexts',cleanup)
    def clear():
        try:manager.clear_all()
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=clear)
    try:
        worker.start();assert entered.wait(2)
        assert manager.enqueue_listing_video(dict(url=url,title='New'),str(tmp_path),manager.cancellation_epoch)
        manager._complete_download(old,'已取消')
        assert manager.active_count==1 and started==[old]
        assert manager.get_items()[0].state=='等待中' and not manager.get_items()[0].error
        release.set();worker.join(3)
        assert not worker.is_alive() and not errors and len(started)==2
        assert manager._active[url] is started[1] and manager.pending_count==0
    finally:
        release.set();worker.join(4)


def test_old_row_identity_cannot_overwrite_readded_same_epoch_row(tmp_path):
    manager=DownloadManager(max_concurrent=2)
    started=[];manager._start_download_thread=started.append
    url='https://jable.tv/videos/handoff-003/'
    old_item=manager.add_item(url);manager.enqueue(url,str(tmp_path));old=started[0]
    manager.remove_item(url)
    assert manager.enqueue_listing_video(dict(url=url,title='New'),str(tmp_path),manager.cancellation_epoch)
    new=manager.get_items()[0]
    assert new.job_id!=old_item.job_id and manager.pending_count==1
    manager._complete_download(old,'未完成',error='old row error')
    assert manager._active[url] is started[1] and not new.error


def test_overlapping_cancel_cleanups_all_finish_before_same_url_handoff(tmp_path,monkeypatch):
    manager=DownloadManager(max_concurrent=2)
    started=[];manager._start_download_thread=started.append
    url='https://jable.tv/videos/handoff-004/'
    manager.add_item(url);manager.enqueue(url,str(tmp_path));old=started[0]
    entered=[threading.Event(),threading.Event()]
    release=[threading.Event(),threading.Event()]
    finished=[threading.Event(),threading.Event()]
    calls=[];lock=threading.Lock();errors=[]
    def cleanup(contexts,cleanup=True):
        with lock:
            index=len(calls);calls.append(contexts)
        entered[index].set();assert release[index].wait(4)
    monkeypatch.setattr(manager,'_cancel_contexts',cleanup)
    def clear(index):
        try:manager.clear_all()
        except Exception as error:errors.append(error)
        finally:finished[index].set()
    workers=[threading.Thread(target=clear,args=(i,)) for i in range(2)]
    try:
        workers[0].start();assert entered[0].wait(2)
        workers[1].start();assert entered[1].wait(2)
        assert manager.enqueue_listing_video(dict(url=url,title='New'),str(tmp_path),manager.cancellation_epoch)
        manager._complete_download(old,'已取消')
        release[0].set();assert finished[0].wait(2)
        assert started==[old] and manager.active_count==1
        release[1].set()
        for worker in workers:worker.join(3)
        assert not errors and not any(worker.is_alive() for worker in workers)
        assert len(started)==2 and manager._active[url] is started[1]
    finally:
        for event in release:event.set()
        for worker in workers:
            if worker.ident is not None:worker.join(4)


def test_cleanup_exception_does_not_strand_same_url_replacement(tmp_path,monkeypatch):
    manager=DownloadManager(max_concurrent=2)
    started=[];manager._start_download_thread=started.append
    url='https://jable.tv/videos/handoff-005/'
    manager.add_item(url);manager.enqueue(url,str(tmp_path));old=started[0]
    def failed(contexts,cleanup=True):
        raise RuntimeError('synthetic cleanup error')
    monkeypatch.setattr(manager,'_cancel_contexts',failed)
    with pytest.raises(RuntimeError,match='synthetic cleanup'):
        manager.clear_all()
    assert manager.enqueue_listing_video(dict(url=url,title='New'),str(tmp_path),manager.cancellation_epoch)
    manager._complete_download(old,'已取消')
    assert len(started)==2 and manager._active[url] is started[1]


def test_retiring_url_does_not_block_other_urls_with_free_capacity(tmp_path):
    manager=DownloadManager(max_concurrent=2)
    started=[];manager._start_download_thread=started.append
    a,b='https://jable.tv/videos/handoff-006/','https://jable.tv/videos/handoff-007/'
    manager.add_item(a);manager.enqueue(a,str(tmp_path));old=started[0]
    manager.clear_all()
    assert manager.enqueue_listing_video(dict(url=a),str(tmp_path),manager.cancellation_epoch)
    assert manager.enqueue_listing_video(dict(url=b),str(tmp_path),manager.cancellation_epoch)
    assert [task.url for task in started]==[a,b] and manager.pending_count==1
    manager._complete_download(old,'已取消')
    assert [task.url for task in started]==[a,b,a] and manager.pending_count==0


@pytest.mark.parametrize('result',['empty','error','repeat'])
def test_compat_empty_failed_or_repeated_pages_end_without_stale_crawling_status(monkeypatch,result):
    app,started,messages,callbacks=app_fixture(monkeypatch)
    calls=[]
    def fetch(page):
        calls.append(page)
        if result=='error':raise ConnectionError('synthetic request failure')
        return [] if result=='empty' else [dict(url='https://missav.ai/en/abc-1')]*2
    monkeypatch.setattr(browse.MissAVBrowser,'fetch_page',fetch)
    browse.ModernApp._crawl_listing(app,'https://missav.ai/en/new','download')
    for callback in callbacks:callback()
    assert not app._listing_requests and messages
    assert messages[-1]!=browse.T('crawling_url')
    assert len(started)==(1 if result=='repeat' else 0)
    assert len(calls)==(2 if result=='repeat' else 1)


def test_two_registered_compat_producers_stop_before_delayed_page_returns(monkeypatch):
    app,started,messages,callbacks=app_fixture(monkeypatch)
    entered=[threading.Event(),threading.Event()];release=threading.Event()
    requests=[browse.ModernApp._register_listing(app,f'https://missav.ai/en/new?run={i}','download') for i in range(2)]
    def fetch(page):
        index=int(threading.current_thread().name.rsplit('-',1)[-1])
        entered[index].set();assert release.wait(3)
        return [dict(url=f'https://missav.ai/en/abc-{index+1}')]
    monkeypatch.setattr(browse.MissAVBrowser,'fetch_page',fetch)
    errors=[]
    def run(request):
        try:browse.ModernApp._crawl_listing(app,request.url,request.destination,request=request)
        except Exception as error:errors.append(error)
    workers=[threading.Thread(target=run,args=(request,),name=f'producer-{i}') for i,request in enumerate(requests)]
    try:
        for worker in workers:worker.start()
        assert all(event.wait(2) for event in entered)
        browse.ModernApp._cancel_all(app)
        release.set()
        for worker in workers:worker.join(3)
        for callback in callbacks:callback()
        assert not errors and not started and not messages
        assert not app._listing_requests and all(request.done.result(0)==0 for request in requests)
        assert not any(worker.is_alive() for worker in workers)
    finally:
        release.set()
        for worker in workers:
            if worker.ident is not None:worker.join(4)
