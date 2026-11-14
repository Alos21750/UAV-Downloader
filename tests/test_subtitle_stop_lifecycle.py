"""Subtitle resource owners must not unwind while their native child writes."""
import threading
import subprocess
import sys
import multiprocessing
import time

import pytest

from uav_downloader.subtitles import engine
from test_http_stop_lifecycle import trickle_server, loopback_tls_material


def test_already_cancelled_audio_helper_does_not_spawn(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(engine.subprocess,'Popen',lambda *a,**kw:calls.append(True))
    with pytest.raises(engine.SubtitleCancelled):
        engine._run_process(['controlled'],str(tmp_path/'log'),lambda:True)
    assert calls==[]


def test_failed_native_kill_keeps_log_and_owner_until_exit(tmp_path,monkeypatch):
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    logs,errors=[],[]
    class Process:
        returncode=None
        def poll(self):return self.returncode
        def terminate(self):raise OSError('controlled terminate failure')
        def kill(self):raise OSError('controlled kill failure')
        def wait(self,timeout=None):
            entered.set()
            if not release.wait(.02):raise subprocess.TimeoutExpired('controlled',timeout)
            self.returncode=-1;return -1
    process=Process()
    def spawn(*a,**kw):logs.append(kw['stdout']);return process
    monkeypatch.setattr(engine.subprocess,'Popen',spawn)
    monkeypatch.setattr(engine,'_wait_for_process',lambda *a,**kw:(_ for _ in ()).throw(engine.SubtitleCancelled()))
    def run():
        try:engine._run_process(['controlled'],str(tmp_path/'log'),None)
        except BaseException as exc:errors.append(exc)
        finally:finished.set()
    worker=threading.Thread(target=run)
    try:
        worker.start();assert entered.wait(2)
        assert not finished.wait(.15) and not logs[0].closed
        release.set();worker.join(2)
        assert not worker.is_alive() and logs[0].closed
        assert len(errors)==1 and isinstance(errors[0],engine.SubtitleCancelled)
    finally:release.set();worker.join(3)


def test_component_trickle_download_stops_before_large_chunk_completes(trickle_server,tmp_path,monkeypatch):
    monkeypatch.setattr(engine,'_session',lambda:trickle_server.session)
    cancelled=threading.Event();errors=[]
    destination=tmp_path/'model.bin'
    def run():
        try:engine._download_verified(trickle_server.url+'/body',str(destination),200,'0'*64,'model',None,cancelled.is_set)
        except BaseException as exc:errors.append(exc)
    worker=threading.Thread(target=run)
    try:
        worker.start();assert trickle_server.started.wait(2)
        cancelled.set();worker.join(.8)
        assert not worker.is_alive() and len(errors)==1 and isinstance(errors[0],engine.SubtitleCancelled)
        assert not destination.exists() and not (tmp_path/'model.bin.part').exists()
    finally:
        cancelled.set();trickle_server.release();worker.join(3)


def test_real_subtitle_helper_is_reaped_before_cancel_returns(tmp_path,monkeypatch):
    original=subprocess.Popen
    entered,cancelled=threading.Event(),threading.Event()
    children,errors=[],[]
    def spawn(*a,**kw):
        child=original([sys.executable,'-c','import time;time.sleep(30)'],**kw)
        children.append(child);entered.set();return child
    monkeypatch.setattr(engine.subprocess,'Popen',spawn)
    def run():
        try:engine._run_process(['controlled'],str(tmp_path/'log'),cancelled.is_set)
        except BaseException as exc:errors.append(exc)
    worker=threading.Thread(target=run)
    try:
        worker.start();assert entered.wait(2)
        cancelled.set();worker.join(3)
        assert not worker.is_alive() and children[0].returncode is not None
        assert len(errors)==1 and isinstance(errors[0],engine.SubtitleCancelled)
    finally:
        cancelled.set()
        for child in children:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)
        worker.join(3)


@pytest.mark.parametrize('cancel', [True, False])
def test_external_translation_trickle_obeys_stop_and_whole_request_budget(trickle_server,cancel):
    from uav_downloader.subtitles import llm
    cancelled=threading.Event();errors=[]
    settings=llm.LlmTranslationSettings(enabled=True,provider='openai-compatible',model='controlled',
        base_url=trickle_server.url+'/v1',timeout_seconds=1,connect_timeout_seconds=.5,max_retries=0)
    def run():
        try:llm.translate_cues(['controlled cue'],settings,session=trickle_server.session,cancel_check=cancelled.is_set)
        except BaseException as error:errors.append(error)
    worker=threading.Thread(target=run)
    try:
        worker.start();assert trickle_server.started.wait(2)
        if cancel:cancelled.set()
        worker.join(.8 if cancel else 1.8)
        assert not worker.is_alive() and len(errors)==1
        assert isinstance(errors[0],llm.LlmTranslationCancelled if cancel else llm.LlmTranslationError)
        assert 'controlled cue' not in str(errors[0])
    finally:
        cancelled.set();trickle_server.release();worker.join(3)


def test_spawned_translation_worker_is_joined_before_handle_release():
    child=multiprocessing.get_context('spawn').Process(target=time.sleep,args=(30,))
    child.start()
    try:
        assert engine._terminate_translation_worker(child) is True
        assert not child.is_alive() and child.exitcode is not None
    finally:
        if child.is_alive():child.kill();child.join(3)
        child.close()
