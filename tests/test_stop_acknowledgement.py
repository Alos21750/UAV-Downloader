"""A stop request is not success until live I/O and owned workers retire."""
from concurrent.futures import TimeoutError as FutureTimeout
import threading

import pytest

from uav_downloader.core.stop import StopContext,StopReason
from uav_downloader.apps.browse import DownloadManager
from uav_downloader.apps import browse as task_queue






def test_stop_finish_requires_all_nested_io_leases_to_exit():
    context=StopContext()
    with context.activity():
        context.request(StopReason.CANCEL)
        with context.activity():
            assert context.active_count==2 and not context.finish()
            assert not context.finished.is_set()
        assert context.active_count==1 and not context.finish()
    assert context.active_count==0 and context.finish() and context.finished.is_set()
