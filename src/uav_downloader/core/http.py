"""Request deadlines and cooperative cancellation without replacing session TLS/proxies.

Requests read timeouts are idle timeouts. A connected socket also needs an
absolute deadline so trickled headers or compressed bodies cannot defeat stop.
Only the owning request closes responses; the observer shuts down its borrowed
socket, never calls Response.close() while another thread holds a read lock.
"""
from contextlib import contextmanager
import socket
import threading
import time
from types import MethodType

import requests
from requests.sessions import Session as RequestsSession

from uav_downloader.core.stop import DownloadStopped, current_stop_context


class RequestDeadlineExceeded(requests.Timeout):
    """The complete request budget expired, irrespective of incoming bytes."""


def close_response(response):
    close = getattr(response, 'close', None)
    if callable(close):
        close()


@contextmanager
def response_scope(response):
    try:
        yield response
    finally:
        close_response(response)


class _Observer:
    """One observer for all concurrent requests; it retires when idle."""
    def __init__(self):
        self.condition = threading.Condition()
        self.scopes = set()
        self.thread = None
        self.generation = 0

    def add(self, scope):
        with self.condition:
            self.scopes.add(scope)
            if self.thread is None:
                generation = self.generation
                self.thread = threading.Thread(target=self._run, args=(generation,),
                                               name='http-stop-observer', daemon=True)
                self.thread.start()
            self.condition.notify_all()

    def remove(self, scope):
        retiring = None
        with self.condition:
            self.scopes.discard(scope)
            if not self.scopes and self.thread is not None:
                retiring = self.thread
                self.thread = None
                self.generation += 1
            self.condition.notify_all()
        if retiring is not None and retiring is not threading.current_thread():
            retiring.join()

    def _run(self, generation):
        while True:
            with self.condition:
                if generation != self.generation:
                    return
                scopes = tuple(self.scopes)
            for scope in scopes:
                scope.interrupt_if_needed()
            with self.condition:
                if generation == self.generation:
                    self.condition.wait(0.02)


_observer = _Observer()
_local = threading.local()
_instrument_lock = threading.Lock()


class RequestBudget:
    def __init__(self, seconds, *, context=None, cancel_check=None, header_seconds=None):
        self.context = context if context is not None else current_stop_context()
        self.cancel_check = cancel_check
        now = time.monotonic()
        self.deadline = now + max(0.001, float(seconds))
        self.header_deadline = min(self.deadline, now + float(header_seconds)) if header_seconds is not None else self.deadline
        self.headers_received = False
        self._lock = threading.RLock()
        self._connections = set()
        self._observed = False
        self._closed = False
        self._cancelled = False

    def remaining(self):
        self.check()
        deadline = self.deadline if self.headers_received else self.header_deadline
        return max(0.001, deadline - time.monotonic())

    def check(self):
        if self._closed:
            return
        if self.context is not None:
            self.context.check()
        if self._cancelled or (self.cancel_check is not None and self.cancel_check()):
            self._cancelled = True
            raise DownloadStopped('peer-stop')
        deadline = self.deadline if self.headers_received else self.header_deadline
        if time.monotonic() >= deadline:
            raise RequestDeadlineExceeded('The complete HTTP request deadline expired')

    def observe(self):
        self._observed = True
        _observer.add(self)

    def attach(self, connection):
        with self._lock:
            connection._uav_request_budget = self
            self._connections.add(connection)

    def detach(self, connection):
        with self._lock:
            self._connections.discard(connection)
            if getattr(connection, '_uav_request_budget', None) is self:
                connection._uav_request_budget = None

    def interrupt_if_needed(self):
        with self._lock:
            if self._closed:
                return
            try:
                self.check()
                return
            except (DownloadStopped, RequestDeadlineExceeded):
                pass
            # Pool return/detach uses this same lock, so we cannot abort a
            # connection that has already been lent to a different request.
            for connection in tuple(self._connections):
                sock = getattr(connection, 'sock', None)
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    def close(self):
        error = None
        try:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                for connection in tuple(self._connections):
                    self.detach(connection)
                    try:
                        connection.close()
                    except BaseException as exc:
                        error = error or exc
        finally:
            if self._observed:
                _observer.remove(self)
        if error is not None:
            raise error


def _instrument_pool(pool):
    with _instrument_lock:
        if getattr(pool, '_uav_request_control', False):
            return pool
        get_connection, put_connection = pool._get_conn, pool._put_conn
        def get_conn(_pool, *args, **kwargs):
            budget = getattr(_local, 'budget', None)
            connection = get_connection(*args, **kwargs)
            if budget is not None:
                try:
                    budget.check()
                except BaseException:
                    connection.close()
                    # urlopen's finally returns one placeholder on failure;
                    # returning another here would grow the pool's queue.
                    raise
                budget.attach(connection)
            return connection
        def put_conn(_pool, connection):
            budget = getattr(connection, '_uav_request_budget', None)
            if budget is not None:
                budget.detach(connection)
            return put_connection(connection)
        pool._get_conn = MethodType(get_conn, pool)
        pool._put_conn = MethodType(put_conn, pool)
        pool._uav_request_control = True
    return pool


def _prepare_session(session):
    # Preserve the actual adapter, SSLContext, cookie jar, proxy managers and
    # CloudScraper behavior. Instrument each returned pool, including existing
    # pools and proxy pools; never replace or clear a live session's adapter.
    with _instrument_lock:
        for adapter in session.adapters.values():
            if getattr(adapter, '_uav_request_control', False):
                continue
            for name in ('get_connection', 'get_connection_with_tls_context'):
                original = getattr(adapter, name, None)
                if original is None:
                    continue
                def wrap(original):
                    def connection(_adapter, *args, **kwargs):
                        return _instrument_pool(original(*args, **kwargs))
                    return connection
                setattr(adapter, name, MethodType(wrap(original), adapter))
            adapter._uav_request_control = True


def _bind_response(response, budget):
    original_close = getattr(response, 'close', None)
    original_iter = getattr(response, 'iter_content', None)
    closed = False
    def close():
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            if callable(original_close):
                original_close()
        finally:
            budget.close()
    response.close = close
    if callable(original_iter):
        def chunks(*args, **kwargs):
            try:
                for chunk in original_iter(*args, **kwargs):
                    budget.check()
                    yield chunk
                budget.check()
            except Exception:
                budget.check()
                raise
            finally:
                close()
        response.iter_content = chunks
    return response


def controlled_request(session, method, url, *, timeout=20, stream=False,
                       total_timeout=None, context=None, cancel_check=None, **kwargs):
    """Buffered responses return closed with cached content; streams close on EOF.

    A streaming caller must still close on early exit. Native DNS/connect calls
    that have not exposed a socket cannot be force-killed: their I/O owner stays
    alive, and the service's bounded stop wait reports that accurately.
    """
    header_seconds = sum(timeout) if isinstance(timeout, tuple) else timeout
    budget = RequestBudget(total_timeout if total_timeout is not None else header_seconds,
                           context=context, cancel_check=cancel_check, header_seconds=header_seconds)
    response = None
    handed_off = False
    previous = getattr(_local, 'budget', None)
    is_requests = isinstance(session, RequestsSession)
    is_curl = any(cls.__module__.startswith('curl_cffi.') for cls in type(session).__mro__)
    try:
        budget.check()
        if is_requests:
            _prepare_session(session)
        if is_requests or is_curl:
            budget.observe()
        _local.budget = budget
        getter = getattr(session, method.lower())
        remaining = budget.remaining()
        call_kwargs = dict(kwargs, timeout=timeout if isinstance(timeout, tuple) else remaining)
        curl_chunks = []
        if is_requests:
            call_kwargs['stream'] = True
        elif is_curl and not stream:
            from curl_cffi.curl import CURL_WRITEFUNC_ERROR
            def receive(chunk):
                try:
                    budget.check()
                except (DownloadStopped, RequestDeadlineExceeded):
                    return CURL_WRITEFUNC_ERROR
                curl_chunks.append(chunk)
                return len(chunk)
            call_kwargs['content_callback'] = receive
        if stream:
            call_kwargs['stream'] = True
        response = getter(url, **call_kwargs)
        budget.check()
        budget.headers_received = True
        response = _bind_response(response, budget)
        if is_curl and not stream:
            response.content = b''.join(curl_chunks)
        if stream:
            handed_off = True
            return response
        # Session hooks, redirects and CloudScraper may have read content
        # already. Requests caches the body; closing keeps .text/.json usable.
        response.content
        budget.check()
        response.close()
        return response
    except BaseException as error:
        error_response = getattr(error, 'response', None)
        if error_response is not None and error_response is not response:
            close_response(error_response)
        try:
            budget.check()
        except (DownloadStopped, RequestDeadlineExceeded) as stopped:
            raise stopped from error
        raise
    finally:
        _local.budget = previous
        if not handed_off:
            try:
                close_response(response)
            finally:
                budget.close()


def controlled_get(session, url, **kwargs):
    return controlled_request(session, 'get', url, **kwargs)
