"""Bounded, cancellable text/CSV URL imports; publishing uses the task actor."""
from concurrent.futures import Future
from dataclasses import dataclass, field
import queue
import csv
import re
import threading

from uav_downloader import sites


def input_urls(text, allow_listing=True):
    from uav_downloader.core.browse_service import listing_site
    from uav_downloader.i18n.locales import T
    result = []
    for raw in str(text).split():
        url = raw.rstrip('.,;)\'"')
        if len(url) > 2048 or not (sites.VaildateUrl(url) or (allow_listing and listing_site(url))):
            raise ValueError(T('url_not_supported'))
        if url not in result:
            result.append(url)
    return result


def file_urls(paths, stopped):
    from uav_downloader.apps.downloads import load_url_file
    for path in paths:
        # Retain upstream UTF-8/CSV/JSON parsing and its 16 MiB/5000 URL caps.
        # Parsing happens on this worker, never on the GUI event loop.
        for url in load_url_file(path):
            if stopped.is_set():
                return
            if sites.VaildateUrl(url):
                yield url


@dataclass(eq=False)
class FileRequest:
    paths: tuple
    destination: str
    epoch: int
    stopped: threading.Event = field(default_factory=threading.Event)
    done: Future = field(default_factory=Future)
    paused: bool = False


class FileImporter:
    def __init__(self, publish, fail, reader=file_urls, wait_dispatch=None):
        self.publish, self.fail, self.reader = publish, fail, reader
        self.wait_dispatch = wait_dispatch
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._requests = set()
        self._closed = False
        self._added = 0
        self._thread = threading.Thread(target=self._run, name='url-file-import', daemon=True)
        self._thread.start()

    def start(self, paths, destination, epoch):
        request = FileRequest(tuple(paths), destination, epoch)
        with self._lock:
            if self._closed:
                raise RuntimeError('File imports are closing')
            self._requests.add(request)
            self._queue.put(request)
        return request

    def stop_all(self, close=False):
        with self._lock:
            for request in self._requests:
                request.stopped.set()
            self._added = 0
            if close and not self._closed:
                self._closed = True
                self._queue.put(None)

    def wait_stopped(self, timeout=None):
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def snapshot(self):
        with self._lock:
            return dict(running=sum(not r.stopped.is_set() and not r.paused for r in self._requests),
                        paused=sum(r.paused and not r.stopped.is_set() for r in self._requests),
                        stopping=sum(r.stopped.is_set() and not r.done.done() for r in self._requests), added=self._added)

    def _publish(self, request, batch):
        if not self._boundary(request):
            return 0
        with self._lock:
            if request.stopped.is_set():
                return 0
            future = self.publish(request, tuple(batch))
        count = future.result()
        with self._lock:
            if not request.stopped.is_set():
                self._added += count
        return count

    def _boundary(self, request):
        if request.stopped.is_set():
            return False
        if self.wait_dispatch is None:
            return True
        with self._lock:
            request.paused = True
        try:
            return self.wait_dispatch(cancel_check=request.stopped.is_set)
        finally:
            with self._lock:
                request.paused = False

    def _run(self):
        while True:
            request = self._queue.get()
            if request is None:
                return
            added, batch = 0, []
            try:
                if not self._boundary(request):
                    request.done.set_result(0)
                    continue
                iterator = iter(self.reader(request.paths, request.stopped))
                try:
                    while self._boundary(request):
                        try:
                            url = next(iterator)
                        except StopIteration:
                            break
                        if request.stopped.is_set():
                            break
                        batch.append(url)
                        if len(batch) == 100:
                            added += self._publish(request, batch)
                            batch = []
                    if batch and not request.stopped.is_set():
                        added += self._publish(request, batch)
                finally:
                    close = getattr(iterator, 'close', None)
                    if callable(close):
                        close()
                request.done.set_result(added)
            except Exception as error:
                if not request.stopped.is_set():
                    self.fail(request, error)
                    request.done.set_exception(error)
                else:
                    request.done.set_result(added)
            finally:
                with self._lock:
                    self._requests.discard(request)
