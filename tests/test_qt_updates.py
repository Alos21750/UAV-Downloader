import threading
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication
from uav_downloader.core import updater
from uav_downloader.core.update_service import UpdateService
from uav_downloader.ui.qt.update_controller import UpdateController


def test_slow_update_check_and_download_handoff_never_apply_an_update_on_the_gui_thread(monkeypatch, tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(updater, 'is_frozen', lambda: True)
    monkeypatch.setattr(updater, 'current_exe_name', lambda: 'UAV_Browser.exe')
    monkeypatch.setattr('uav_downloader.core.update_service.tempfile.mkdtemp', lambda **kwargs: str(tmp_path))
    info = dict(version='9.0.0', html_url='https://example.test/release',
                assets={'UAV_Browser.exe': 'https://example.test/browser.exe'})
    def check():
        entered.set()
        assert release.wait(3)
        return info
    def download(url, path, progress_cb, cancel_check):
        assert url == info['assets']['UAV_Browser.exe'] and not cancel_check()
        Path(path).write_bytes(b'MZ-synthetic-update')
        progress_cb(100, 100)
        return True
    controller = UpdateController(service=UpdateService('browse', check, download))
    controller.timer.stop()
    handoffs = []
    controller.installReady.connect(lambda: handoffs.append(controller.pending_install))
    try:
        controller.check()
        assert entered.wait(2) and controller.busy
        controller.poll()
        assert controller.busy and not handoffs
        release.set()
        controller._future.result(3)
        controller.poll()
        assert controller.available and controller.canApply
        controller.download()
        assert not handoffs
        controller._future.result(3)
        controller.poll()
        assert handoffs == [str(tmp_path / 'UAV_Browser.exe')]
        assert Path(handoffs[0]).read_bytes().startswith(b'MZ')
    finally:
        release.set()
        controller.close()


def test_preview_executable_cannot_be_replaced_with_a_different_application_asset(monkeypatch):
    monkeypatch.setattr(updater, 'is_frozen', lambda: True)
    monkeypatch.setattr(updater, 'current_exe_name', lambda: 'UAV_Qt_Preview.exe')
    service = UpdateService('browse')
    try:
        assert not service.can_apply()
        with pytest.raises(ValueError, match='unavailable'):
            service.download(dict(assets={'UAV_Browser.exe': 'https://example.test/exe'}))
    finally:
        service.close()


def test_cancelled_update_closes_file_before_removing_partial_download(monkeypatch, tmp_path):
    stopped = threading.Event()
    class Response:
        status_code = 200
        headers = {'content-length': '4000000'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, chunk_size):
            yield b'MZ' + b'x' * 100
            stopped.set()
            yield b'y' * 100
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(updater, '_session', Session)
    monkeypatch.setattr(updater.config, 'proxy_request_kwargs', lambda: {})
    path = tmp_path / 'update.exe'
    assert updater.download_asset('https://example.test/update', str(path), cancel_check=stopped.is_set) is False
    assert not path.exists() and not path.with_name('update.exe.part').exists()
