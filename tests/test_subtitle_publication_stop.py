"""Cancellation observed during I/O must preserve the previous published asset."""
import threading
from pathlib import Path

import pytest

from uav_downloader.core.stop import StopContext, stop_scope
from uav_downloader.subtitles import engine


def test_atomic_sidecar_write_rechecks_stop_after_flush(tmp_path,monkeypatch):
    path=tmp_path/'video.en.srt';path.write_text('previous manual data',encoding='utf-8')
    context=StopContext();real_fsync=engine.os.fsync
    def fsync(fd):
        real_fsync(fd);context.request()
    monkeypatch.setattr(engine.os,'fsync',fsync)
    with stop_scope(context),pytest.raises(engine.SubtitleCancelled):
        engine._atomic_write_text(str(path),'late replacement')
    assert path.read_text(encoding='utf-8')=='previous manual data'
    assert not list(tmp_path.glob('*.tmp'))


def test_stopped_atomic_copy_does_not_touch_existing_file(tmp_path):
    source,target=tmp_path/'source.srt',tmp_path/'video.ja.srt'
    source.write_bytes(b'new');target.write_bytes(b'old')
    context=StopContext();context.request()
    with stop_scope(context),pytest.raises(engine.SubtitleCancelled):
        engine._atomic_copy(str(source),str(target))
    assert source.read_bytes()==b'new' and target.read_bytes()==b'old'
    assert not list(tmp_path.glob('*.tmp'))


def test_standalone_translation_callback_controls_final_write(tmp_path,monkeypatch):
    source,target=tmp_path/'source.srt',tmp_path/'video.en.srt'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\nsource\n',encoding='utf-8')
    target.write_text('preserved',encoding='utf-8')
    cancelled=threading.Event()
    monkeypatch.setattr(engine,'translate_cues',lambda *a,**kw:['translated'])
    real_fsync=engine.os.fsync
    def fsync(fd):real_fsync(fd);cancelled.set()
    monkeypatch.setattr(engine.os,'fsync',fsync)
    with pytest.raises(engine.SubtitleCancelled):
        engine.translate_srt(str(source),str(target),'en','translate_en',cancel_check=cancelled.is_set)
    assert target.read_text(encoding='utf-8')=='preserved'


@pytest.mark.parametrize('kind',['translation','reazon'])
def test_stop_while_marking_verified_model_does_not_activate_it(tmp_path,monkeypatch,kind):
    cancelled=threading.Event()
    monkeypatch.setattr(engine,'_cache_root',lambda:str(tmp_path))
    monkeypatch.setattr(engine,'_component_install_candidates',lambda *a:[])
    monkeypatch.setattr(engine,'_find_verified_component_archive',lambda *a:'controlled.zip')
    monkeypatch.setattr(engine,'_safe_extract_zip',lambda *a,**kw:None)
    monkeypatch.setattr(engine,'_ensure_asr_temp_space',lambda *a:None)
    def verify(path,**kwargs):
        if kwargs.get('require_marker') is False:return {} if kind=='translation' else str(path)
        raise engine.SubtitleError('controlled missing installation')
    monkeypatch.setattr(engine,'_verify_translation_install' if kind=='translation' else '_verify_reazonspeech_install',verify)
    real_open=open
    class Marker:
        def __init__(self,*a,**kw):self.file=real_open(*a,**kw)
        def __enter__(self):return self
        def write(self,value):return self.file.write(value)
        def flush(self):return self.file.flush()
        def fileno(self):return self.file.fileno()
        def __exit__(self,*a):self.file.close();cancelled.set()
    def opened(path,*a,**kw):
        return Marker(path,*a,**kw) if str(path).endswith('.source-sha256') else real_open(path,*a,**kw)
    monkeypatch.setattr(engine,'open',opened,raising=False)
    function=engine._prepare_translation_runtime_locked if kind=='translation' else engine._prepare_reazonspeech_runtime_locked
    with pytest.raises(engine.SubtitleCancelled):function(None,cancelled.is_set)
    assert not list(tmp_path.rglob('.source-sha256'))
