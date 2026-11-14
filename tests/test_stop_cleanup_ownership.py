"""The manager requests cancellation; each downloader owns its cleanup."""
from types import SimpleNamespace
import threading

import pytest

from uav_downloader.core.stop import StopContext
from uav_downloader.apps.browse import DownloadManager


@pytest.mark.parametrize('cleanup', [False, True])
def test_manager_does_not_repeat_downloader_cleanup(cleanup):
    calls = []

    class Job:
        def cancel_download(self, cleanup=True):
            calls.append(('cancel', cleanup))
            if cleanup:
                self.cleanup_temp()

        def cleanup_temp(self):
            calls.append(('cleanup', True))

    task = SimpleNamespace(cancelled=threading.Event(),
                           monitor_stop=threading.Event(),
                           stop=StopContext(), pause_requested=False, job=Job())
    DownloadManager._cancel_contexts([task, task], cleanup=cleanup)
    assert calls == ([('cancel', True), ('cleanup', True)] if cleanup else [('cancel', False)])


def test_legacy_cancel_signature_does_not_allow_manager_to_delete_live_cache():
    calls = []

    class Job:
        def cancel_download(self):
            calls.append('requested')

        def cleanup_temp(self):
            pytest.fail('legacy cancellation does not acknowledge that its writer has exited')

    task = SimpleNamespace(cancelled=threading.Event(),
                           monitor_stop=threading.Event(),
                           stop=StopContext(), pause_requested=False, job=Job())
    DownloadManager._cancel_contexts([task], cleanup=True)
    assert calls == ['requested']


@pytest.mark.parametrize('failure', ['request', 'cancel'])
def test_stop_callback_type_error_is_not_retried_as_a_legacy_signature(failure):
    calls = []

    class Job:
        def request_stop(self, reason, *, cleanup):
            calls.append('request')
            if failure == 'request':
                raise TypeError('controlled internal callback failure')

        def cancel_download(self, cleanup=True):
            calls.append(('cancel', cleanup))
            if failure == 'cancel':
                raise TypeError('controlled internal callback failure')

    task = SimpleNamespace(cancelled=threading.Event(), monitor_stop=threading.Event(),
                           stop=StopContext(), pause_requested=False, job=Job())
    DownloadManager._cancel_contexts([task], cleanup=False)
    assert calls == ['request', ('cancel', False)]
