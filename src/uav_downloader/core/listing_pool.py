"""Bounded parallel listing requests; all returned batches go through the task actor."""
from dataclasses import dataclass, field
import queue
import threading
import time
from concurrent.futures import Future
from itertools import count, islice

from uav_downloader.core.browse_service import fetch_listing_page
from contextlib import nullcontext


@dataclass(eq=False)
class ListingRequest:
    site: str
    url: str
    destination: str
    epoch: int
    stopped: threading.Event = field(default_factory=threading.Event)
    done: Future = field(default_factory=Future)
    paused: bool = False
    not_before: float = 0
    resolution: str = ''
    filename_template: str = ''


class ListingPool:
    def __init__(self, publish, fail, fetcher=fetch_listing_page, workers=2, max_pages=None, wait_dispatch=None):
        self.publish, self.fail, self.fetcher = publish, fail, fetcher
        self.max_pages = max_pages
        self.wait_dispatch = wait_dispatch
        self._lock = threading.Lock()
        self._requests = set()
        self._queue = queue.Queue()
        self._closed = False
        self._added = 0
        self._threads = [threading.Thread(target=self._run, name=f'listing-{index}', daemon=True)
                         for index in range(workers)]
        for thread in self._threads:
            thread.start()

    def start(self, site, url, destination, epoch, *, not_before=0, resolution='', filename_template=''):
        request = ListingRequest(site, url, destination, epoch, not_before=not_before,
                                 resolution=resolution, filename_template=filename_template)
        with self._lock:
            if self._closed:
                raise RuntimeError('Listing requests are closing')
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
                for _ in self._threads:
                    self._queue.put(None)

    def wait_stopped(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        for thread in self._threads:
            thread.join(None if deadline is None else max(0.0, deadline-time.monotonic()))
            if thread.is_alive():
                return False
        return True

    def snapshot(self):
        with self._lock:
            return dict(running=sum(not request.stopped.is_set() and not request.paused for request in self._requests),
                        paused=sum(request.paused and not request.stopped.is_set() for request in self._requests),
                        stopping=sum(request.stopped.is_set() and not request.done.done() for request in self._requests),
                        added=self._added)

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
            if not request.done.set_running_or_notify_cancel():
                with self._lock:
                    self._requests.discard(request)
                continue
            added = 0
            seen = set()
            try:
                with nullcontext():
                    for page in islice(count(1), self.max_pages):
                        if not self._boundary(request):
                            break
                        videos = self.fetcher(request.site, request.url, page)
                        if request.stopped.is_set() or not videos:
                            break
                        batch = []
                        for video in videos:
                            url = video.get('url', '')
                            if url and url not in seen:
                                seen.add(url)
                                batch.append(dict(video))
                        if not batch or request.stopped.is_set():
                            break
                        if not self._boundary(request):
                            break
                        # At most one outstanding batch per worker keeps
                        # command/memory growth bounded during a fast fetch.
                        with self._lock:
                            if request.stopped.is_set():
                                break
                            published = self.publish(request, batch)
                        published_count = published.result()
                        added += published_count
                        with self._lock:
                            if not request.stopped.is_set():
                                self._added += published_count
                        if request.stopped.wait(.3):
                            break
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
