"""Frozen startup/close race: workers must never call Tk from their thread."""
import queue
import threading
from types import SimpleNamespace
import pytest
from uav_downloader.apps import browse


def test_worker_dispatch_uses_queue_without_tk_calls():
    callbacks = queue.SimpleQueue()
    errors, executed = [], []
    def after(*args):
        raise RuntimeError('worker must not enter Tk')
    app = SimpleNamespace(_is_closing=False, _build_gen=1,
                          _ui_callbacks=callbacks, after=after)
    def worker():
        try:
            browse.ModernApp._ui(app, lambda: executed.append(1), gen=1)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(1)
    assert not errors
    assert not executed
    assert not callbacks.empty()


def test_mainloop_close_during_customtkinter_setup_is_normal(monkeypatch):
    class App:
        _is_closing = False
        def __init__(self, **kwargs):
            pass
        def mainloop(self):
            self._is_closing = True
            raise browse.tk.TclError('application has been destroyed')
    monkeypatch.setattr(browse, 'ModernApp', App)
    browse.gui_modern_main()


def test_mainloop_real_tcl_failure_is_not_hidden(monkeypatch):
    class App:
        _is_closing = False
        def __init__(self, **kwargs):
            pass
        def mainloop(self):
            raise browse.tk.TclError('unexpected failure')
    monkeypatch.setattr(browse, 'ModernApp', App)
    with pytest.raises(browse.tk.TclError):
        browse.gui_modern_main()


def test_close_during_widget_construction_is_deferred():
    app = SimpleNamespace(_startup_complete=False, _is_closing=False,
                          _close_requested=False)
    browse.ModernApp._on_close(app)
    assert app._is_closing and app._close_requested


def test_drain_discards_stale_and_closing_callbacks():
    called = []
    callbacks = queue.SimpleQueue()
    callbacks.put((0, lambda: called.append('stale')))
    callbacks.put((1, lambda: called.append('current')))
    app = SimpleNamespace(_is_closing=False, _build_gen=1,
                          _ui_callbacks=callbacks, after=lambda *a: None,
                          _drain_ui_callbacks=lambda: None)
    browse.ModernApp._drain_ui_callbacks(app)
    assert called == ['current']
    app._is_closing = True
    callbacks.put((1, lambda: called.append('closed')))
    browse.ModernApp._drain_ui_callbacks(app)
    assert called == ['current']
