"""Real loopback trickle responses exercise deadlines and cancellation."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import socket
import ssl
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import re
import threading
import time

import pytest
import requests

from uav_downloader.core.stop import StopContext, DownloadStopped, stop_scope
from uav_downloader.sites import base, supjav
from uav_downloader.core.http import controlled_get, RequestDeadlineExceeded


@pytest.fixture(scope='session')
def loopback_tls_material(tmp_path_factory):
    executable = shutil.which('openssl')
    if executable is None:
        candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Git/usr/bin/openssl.exe'
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        pytest.skip('OpenSSL CLI is needed to create a temporary loopback TLS certificate')
    directory = tmp_path_factory.mktemp('loopback-tls')
    cert, key = directory/'cert.pem', directory/'key.pem'
    subprocess.run([executable, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                    '-days', '1', '-keyout', str(key), '-out', str(cert)],
                   check=True, capture_output=True, timeout=10, **base._no_window_kwargs())
    return cert, key


@pytest.fixture(params=['http', 'https'])
def trickle_server(monkeypatch, request):
    started, stop = threading.Event(), threading.Event()
    ranges_ready, range_lock = threading.Event(), threading.Lock()
    ranges_started = set()
    options = SimpleNamespace(fail_first_range=False)
    connections, handlers = [], []
    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            import sys
            # Aborting a client can race a server header/body write. Surface
            # unexpected fixture errors; a closed peer is the tested behavior.
            if not isinstance(sys.exc_info()[1], (OSError, ValueError)):
                super().handle_error(request, client_address)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.connection.settimeout(1)
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.do_GET()
        def do_GET(self):
            connections.append(self.connection)
            handlers.append(threading.current_thread())
            if self.path in ('/ok', '/gzip'):
                payload = b'controlled decoded response' * 2048
                body = gzip.compress(payload) if self.path == '/gzip' else payload
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                if self.path == '/gzip':
                    self.send_header('Content-Encoding', 'gzip')
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                started.set()
                return
            if self.path.startswith('/redirect/'):
                count = int(self.path.rsplit('/', 1)[1])
                if stop.wait(0.06):
                    self.close_connection = True
                    return
                self.send_response(302)
                self.send_header('Content-Length', '0')
                self.send_header('Location', '/ok' if count == 1 else f'/redirect/{count-1}')
                self.end_headers()
                return
            try:
                range_request = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
                count = 200
                if range_request:
                    lower = int(range_request[1])
                    upper = int(range_request[2]) if range_request[2] else 799
                    count = upper-lower+1
                    if count != 1:
                        with range_lock:
                            ranges_started.add(lower)
                            if len(ranges_started) == 4:
                                ranges_ready.set()
                    if options.fail_first_range and lower == 0 and count != 1:
                        assert ranges_ready.wait(2)
                        self.send_response(412)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                if self.path == '/headers':
                    self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Trickle: ')
                else:
                    chunk = gzip.compress(b'') if self.path == '/gzip-trickle' else b'x'
                    self.send_response(206 if range_request else 200)
                    if range_request:
                        self.send_header('Content-Range', f'bytes {lower}-{upper}/800')
                    self.send_header('ETag', '"asset-v1"')
                    self.send_header('Content-Length', str(count * len(chunk)))
                    if self.path == '/gzip-trickle':
                        self.send_header('Content-Encoding', 'gzip')
                    self.end_headers()
                self.wfile.flush()
                started.set()
                if count == 1:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    return
                for _ in range(count):
                    if stop.wait(0.02):
                        break
                    self.wfile.write(chunk if self.path != '/headers' else b'x')
                    self.wfile.flush()
            except (OSError, ValueError):
                pass
            finally:
                self.close_connection = True
    server = Server(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02})
    session = requests.Session()
    session.trust_env = False
    session.mount('https://', base.SharedSSLAdapter())
    if request.param == 'https':
        cert, key = request.getfixturevalue('loopback_tls_material')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        session.verify = str(cert)
    monkeypatch.setattr(base, '_use_cffi', False)
    monkeypatch.setattr(base, '_get_session', lambda: session)
    monkeypatch.setattr(base.config, 'proxy_request_kwargs', lambda: {})
    thread.start()
    def release():
        stop.set()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    try:
        yield SimpleNamespace(url=f'{request.param}://127.0.0.1:{server.server_port}',
                              started=started, release=release, session=session,
                              ranges_ready=ranges_ready, options=options)
    finally:
        session.close()
        release()
        server.shutdown()
        server.server_close()
        thread.join(2)
        for handler in handlers:
            handler.join(2)
        assert not thread.is_alive() and not any(handler.is_alive() for handler in handlers)
        from uav_downloader.core.http import _observer
        assert not _observer.scopes
        assert not any(t.name == 'http-stop-observer' and t.is_alive() for t in threading.enumerate())


@contextmanager
def fetching(server, path, context, timeout):
    results, errors = [], []
    def run():
        try:
            with stop_scope(context):
                results.append(base._http_get(server.url + path, timeout=timeout))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert server.started.wait(2)
        yield worker, results, errors
    finally:
        context.request()
        server.release()
        worker.join(3)
        assert not worker.is_alive()


@pytest.mark.parametrize('path', ['/body', '/headers', '/gzip-trickle'])
def test_continuously_trickling_response_has_a_whole_request_deadline(trickle_server, path):
    with fetching(trickle_server, path, StopContext(), timeout=0.2) as (worker, results, errors):
        worker.join(0.8)
        assert not worker.is_alive(), 'per-read timeout must not let trickled bytes defeat the request deadline'
        assert not results and len(errors) == 1 and isinstance(errors[0], requests.Timeout)


@pytest.mark.parametrize('path', ['/body', '/headers', '/gzip-trickle'])
def test_stop_interrupts_trickled_headers_and_body_without_waiting_for_full_content(trickle_server, path):
    context = StopContext()
    with fetching(trickle_server, path, context, timeout=5) as (worker, results, errors):
        context.request()
        worker.join(0.8)
        assert not worker.is_alive(), 'stop must interrupt connected network I/O'
        assert not results and len(errors) == 1 and isinstance(errors[0], DownloadStopped)


def test_buffered_media_response_is_closed_before_return(monkeypatch):
    closed = []
    response = SimpleNamespace(status_code=200, content=b'media', close=lambda: closed.append(True))
    monkeypatch.setattr(base, '_use_cffi', False)
    monkeypatch.setattr(base, '_get_session', lambda: SimpleNamespace(get=lambda *a, **kw: response))
    assert base._http_get('https://example.test/media').content == b'media'
    assert closed


def test_invalid_playlist_response_is_closed_even_when_fetch_is_overridden(monkeypatch):
    closed = []
    response = SimpleNamespace(status_code=404, headers={}, content=b'missing',
                               close=lambda: closed.append(True))
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: response)
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._extra_headers = {}
    with pytest.raises(Exception, match='404'):
        job._load_m3u8('https://example.test/missing')
    assert closed


def test_rejected_segment_response_is_closed(tmp_path, monkeypatch):
    closed = []
    response = SimpleNamespace(status_code=503, close=lambda: closed.append(True))
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: response)
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._temp_folder = str(tmp_path)
    job._extra_headers = {}
    assert job._scrape((0, 'https://example.test/0.ts')) is False
    assert closed


def test_image_read_that_observes_stop_is_not_written(tmp_path, monkeypatch):
    closed = []
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._dest_folder = str(tmp_path)
    job._imageUrl = 'https://example.test/cover.jpg'
    job._extra_headers = {}
    image = tmp_path/'cover.jpg'
    job._get_image_savename = lambda: str(image)
    class Response:
        status_code = 200
        @property
        def content(self):
            job.request_stop()
            return b'late image'
        def close(self):
            closed.append(True)
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: Response())
    job.download_image()
    assert not image.exists() and closed


@pytest.mark.parametrize('path', ['/ok', '/gzip'])
def test_buffered_content_survives_close_and_preserves_the_actual_adapter(trickle_server, path):
    adapter = trickle_server.session.get_adapter(trickle_server.url)
    response = controlled_get(trickle_server.session, trickle_server.url + path, timeout=2)
    assert response.content == b'controlled decoded response' * 2048
    assert response.text == (b'controlled decoded response' * 2048).decode()
    assert trickle_server.session.get_adapter(trickle_server.url) is adapter
    assert response.raw.closed


def test_redirects_share_the_request_deadline(trickle_server):
    started = time.monotonic()
    with pytest.raises(RequestDeadlineExceeded):
        controlled_get(trickle_server.session, trickle_server.url + '/redirect/8', timeout=0.2)
    assert time.monotonic() - started < 0.8


def test_curl_buffered_content_and_native_deadline(trickle_server):
    from curl_cffi.requests import Session
    with Session(impersonate='chrome', verify=trickle_server.session.verify) as session:
        response = controlled_get(session, trickle_server.url + '/gzip', timeout=2,
                                  proxies={'http': '', 'https': ''})
        assert response.content == b'controlled decoded response' * 2048
        with pytest.raises(RequestDeadlineExceeded):
            controlled_get(session, trickle_server.url + '/headers', timeout=0.2,
                           proxies={'http': '', 'https': ''})


def test_curl_body_callback_observes_stop_without_a_requests_fallback(trickle_server, monkeypatch):
    from curl_cffi.requests import Session
    with Session(impersonate='chrome', verify=trickle_server.session.verify) as session:
        monkeypatch.setattr(base, '_use_cffi', True)
        monkeypatch.setattr(base, '_get_cffi_session', lambda: session)
        context = StopContext()
        with fetching(trickle_server, '/body', context, timeout=2) as (worker, results, errors):
            context.request()
            worker.join(0.8)
            assert not worker.is_alive() and not results
            assert len(errors) == 1 and isinstance(errors[0], DownloadStopped)


def test_returned_pool_connection_is_not_aborted_by_its_previous_owner(trickle_server):
    previous, current = StopContext(), StopContext()
    response = controlled_get(trickle_server.session, trickle_server.url + '/ok',
                              timeout=2, stream=True, context=previous)
    chunks = response.iter_content(chunk_size=100000)
    assert next(chunks) == b'controlled decoded response' * 2048
    # urllib3 has returned the connection, while the previous consumer still
    # holds its last yielded chunk and has not closed its iterator/scope yet.
    trickle_server.started.clear()
    try:
        with fetching(trickle_server, '/body', current, timeout=5) as (worker, results, errors):
            previous.request()
            worker.join(0.12)
            assert worker.is_alive() and not errors and not results
            current.request()
            worker.join(0.8)
            assert not worker.is_alive() and isinstance(errors[0], DownloadStopped)
    finally:
        chunks.close()
        response.close()


@pytest.mark.parametrize('parallel', [False, True])
def test_direct_transfer_cancels_real_streaming_readers_and_closes_responses(trickle_server, tmp_path, monkeypatch, parallel):
    monkeypatch.setattr(supjav, '_get_session', lambda: trickle_server.session)
    responses, errors, results = [], [], []
    trickle_server.session.hooks['response'].append(lambda response, **kw: responses.append(response))
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._cancel_job = False
    job._direct_url = trickle_server.url + ('/range' if parallel else '/body')
    job._direct_expected_etag = '"asset-v1"'
    job._max_workers = 4
    job._progress_callback = None
    part = str(tmp_path/'direct.part')
    def transfer():
        try:
            target = job._download_direct_ranges if parallel else job._download_direct_serial
            results.append(target(part, trickle_server.url, time.time()))
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=transfer)
    try:
        worker.start()
        assert (trickle_server.ranges_ready if parallel else trickle_server.started).wait(2)
        job.request_stop()
        worker.join(0.8)
        assert not worker.is_alive() and not errors
        assert results and responses and all(response.raw.closed for response in responses)
        if parallel:
            assert len(responses) >= 4 and job._t2_executor is None
    finally:
        job.request_stop()
        trickle_server.release()
        worker.join(3)


def test_one_failed_range_interrupts_other_connected_ranges(trickle_server, tmp_path, monkeypatch):
    from uav_downloader.core.download_storage import ResourceChangedError
    monkeypatch.setattr(supjav, '_get_session', lambda: trickle_server.session)
    trickle_server.options.fail_first_range = True
    errors = []
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._cancel_job = False
    job._direct_url = trickle_server.url + '/range'
    job._direct_expected_etag = '"asset-v1"'
    job._max_workers = 4
    job._progress_callback = None
    def transfer():
        try:
            job._download_direct_ranges(str(tmp_path/'direct.part'), trickle_server.url, time.time())
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=transfer)
    try:
        worker.start()
        assert trickle_server.ranges_ready.wait(2)
        worker.join(0.8)
        assert not worker.is_alive() and len(errors) == 1 and isinstance(errors[0], ResourceChangedError)
        assert not job._cancel_job and job._t2_executor is None
    finally:
        job.request_stop()
        trickle_server.release()
        worker.join(3)


def test_native_resolution_without_a_socket_never_fakes_worker_exit(trickle_server, tmp_path, monkeypatch):
    from uav_downloader.apps import browse as task_queue
    from uav_downloader.apps.browse import DownloadManager
    from uav_downloader.core.stop import StopTimeoutError
    entered, release = threading.Event(), threading.Event()
    original_resolve = socket.getaddrinfo
    def resolve(host, *args, **kwargs):
        if host == '127.0.0.1':
            entered.set()
            assert release.wait(3)
        return original_resolve(host, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(task_queue.config, 'get_minimum_free_gb', lambda: 0)
    class Site(base.M3U8Crawler):
        website_dirname_pattern = r'https://jable\.tv/videos/([^/]+)/'
        def get_url_infos(self):
            self._targetName = 'Controlled resolution'
            self._m3u8url = trickle_server.url + '/playlist'
        def _start_download_scoped(self):
            return base._http_get(trickle_server.url + '/ok', timeout=0.2)
    monkeypatch.setattr(task_queue.M3U8Sites, 'CreateSite', lambda url, dest, **kw: Site(url, savepath=dest, silence=True))
    manager = DownloadManager(subtitle_mode_getter=lambda: 'none', slow_requeue_kbps_getter=lambda: 0,
                              auto_retry_count_getter=lambda: 0)
    try:
        manager.enqueue('https://jable.tv/videos/native-resolve/', str(tmp_path))
        assert entered.wait(2)
        manager.cancel_all(cleanup=False)
        assert not manager.wait_stopped(timeout=0.05)
        assert manager.active_count == 1
        task = next(iter(manager._active.values()))
        assert not task.stop.finished.is_set()
        release.set()
        assert manager.wait_stopped(timeout=2) and manager.active_count == 0
        assert manager.wait_stopped(timeout=1) and task.stop.finished.is_set()
    finally:
        release.set()
        manager.wait_stopped(timeout=3)


def test_cffi_and_requests_fallback_share_one_budget(trickle_server, monkeypatch):
    def fail_curl(*args, **kwargs):
        threading.Event().wait(0.12)
        raise ConnectionError('controlled cffi failure')
    monkeypatch.setattr(base, '_use_cffi', True)
    monkeypatch.setattr(base, '_get_cffi_session', lambda: SimpleNamespace(get=fail_curl))
    started = time.monotonic()
    with pytest.raises(RequestDeadlineExceeded):
        base._http_get(trickle_server.url + '/body', timeout=0.2)
    assert time.monotonic() - started < 0.8


@pytest.mark.parametrize('trickle_server', ['http'], indirect=True)
def test_connected_proxy_obeys_the_same_deadline(trickle_server):
    with pytest.raises(RequestDeadlineExceeded):
        controlled_get(trickle_server.session, 'http://unresolved.example.invalid/body', timeout=0.2,
                       proxies={'http': trickle_server.url})


def test_error_response_attached_to_a_transport_exception_is_closed():
    closed = []
    response = SimpleNamespace(close=lambda: closed.append(True))
    def get(*args, **kwargs):
        raise requests.HTTPError('controlled status failure', response=response)
    with pytest.raises(requests.HTTPError):
        controlled_get(SimpleNamespace(get=get), 'https://example.test/missing')
    assert closed == [True]


def test_failed_key_response_is_closed(tmp_path, monkeypatch):
    closed = []
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._m3u8url = 'https://example.test/playlist'
    job._extra_headers = {}
    job._load_m3u8 = lambda _: SimpleNamespace(playlists=[], keys=[SimpleNamespace(uri='https://example.test/key')])
    response = SimpleNamespace(status_code=404, headers={}, content=b'missing', close=lambda: closed.append(True))
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: response)
    with pytest.raises(Exception, match='AES'):
        job._create_m3u8()
    assert closed == [True]


def test_stop_during_image_write_never_publishes_a_partial_or_deletes_unknown_files(tmp_path, monkeypatch):
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._dest_folder = str(tmp_path)
    job._imageUrl = 'https://example.test/cover.jpg'
    job._extra_headers = {}
    image = tmp_path/'cover.jpg'
    unknown = tmp_path/'cover.jpg.part'
    unknown.write_bytes(b'valuable old partial')
    job._get_image_savename = lambda: str(image)
    closed, writer_closed = [], []
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: SimpleNamespace(
        status_code=200, content=b'controlled image', close=lambda: closed.append(True)))
    original_open = base.storage_open
    class Writer:
        def __init__(self, *args, **kwargs):
            self.stream = original_open(*args, **kwargs)
        def __enter__(self):
            self.stream.__enter__()
            return self
        def write(self, content):
            result = self.stream.write(content)
            job.request_stop()
            return result
        def __exit__(self, *args):
            self.stream.__exit__(*args)
            writer_closed.append(True)
    monkeypatch.setattr(base, 'storage_open', Writer)
    assert job.download_image() is None
    assert closed == [True] and writer_closed == [True]
    assert not image.exists() and unknown.read_bytes() == b'valuable old partial'
    assert not list(tmp_path.glob('.uav-cover-*.part'))




def test_compat_listing_stop_reaches_its_connected_fetch(trickle_server, tmp_path, monkeypatch):
    from uav_downloader.apps import browse
    from uav_downloader.apps.browse import DownloadManager
    manager = DownloadManager()
    app = SimpleNamespace(_is_closing=False, _build_gen=1, _dlmgr=manager,
                          _listing_lock=threading.Lock(), _listing_requests=set(),
                          _listing_status_request=None, _ui=lambda *a, **kw: None)
    def fetch(*args):
        base._http_get(trickle_server.url + '/body', timeout=5)
        return []
    monkeypatch.setattr(browse.MissAVBrowser, 'fetch_page', fetch)
    request = browse.ModernApp._register_listing(app, 'https://missav.ai/en/new', str(tmp_path))
    worker = threading.Thread(target=browse.ModernApp._crawl_listing,
                              args=(app, request.url, request.destination), kwargs={'request': request})
    try:
        worker.start()
        assert trickle_server.started.wait(2)
        browse.ModernApp._stop_listing_requests(app)
        worker.join(0.8)
        assert not worker.is_alive() and request.done.result(0) == 0
        assert request.stop.finished.is_set() and not manager.get_items()
    finally:
        browse.ModernApp._stop_listing_requests(app)
        trickle_server.release()
        worker.join(3)
        assert not worker.is_alive()


@pytest.mark.parametrize('curl', [False, True])
def test_session_setup_does_not_reset_the_outer_media_request_budget(monkeypatch, curl):
    calls = []
    response = SimpleNamespace(content=b'media', close=lambda: None)
    def build_session():
        threading.Event().wait(0.04)
        return SimpleNamespace(get=lambda *a, **kw: calls.append(True) or response)
    monkeypatch.setattr(base, '_use_cffi', curl)
    monkeypatch.setattr(base, '_get_cffi_session' if curl else '_get_session', build_session)
    with pytest.raises(RequestDeadlineExceeded):
        base._http_get('https://example.test/media', timeout=0.01)
    assert calls == []
