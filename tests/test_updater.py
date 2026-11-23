# coding: utf-8
"""Auto-updater tests: version compare (drives whether users are offered updates) and
download_asset validation (size floor + MZ-header check + atomic replace)."""
from uav_downloader.core import updater
import io
import pytest


def test_real_encoded_update_checks_compressed_not_decoded_length(monkeypatch, tmp_path):
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    body = b'MZ' + b'abcd' * 750_001
    wire = gzip.compress(body)
    requested_encodings = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested_encodings.append(self.headers.get('Accept-Encoding'))
            self.send_response(200)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = updater.requests.Session()
    session.trust_env = False
    monkeypatch.setattr(updater, '_session', lambda: session)
    monkeypatch.setattr(updater.config, 'proxy_request_kwargs', lambda: {})
    try:
        dest = tmp_path / 'fixture.exe'
        assert updater.download_asset(f'http://127.0.0.1:{server.server_port}/asset', str(dest))
        assert dest.read_bytes() == body
        assert requested_encodings == ['identity']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_parse_version():
    assert updater.parse_version('2.5.18') == (2, 5, 18)
    assert updater.parse_version('v2.5.18') == (2, 5, 18)
    assert updater.parse_version('2.10.0') == (2, 10, 0)
    assert updater.parse_version('2.5') == (2, 5, 0)
    assert updater.parse_version('2') == (2, 0, 0)
    assert updater.parse_version('') == (0, 0, 0)
    assert updater.parse_version(None) == (0, 0, 0)
    assert updater.parse_version('v2.5.18-beta1') == (2, 5, 18)


def test_is_newer_is_numeric_not_lexical():
    assert updater.is_newer('2.10.0', '2.9.0') is True      # 10 > 9 numerically, not "1" < "9"
    assert updater.is_newer('2.5.18', '2.5.17') is True
    assert updater.is_newer('2.5.17', '2.5.18') is False
    assert updater.is_newer('2.5.18', '2.5.18') is False
    assert updater.is_newer('v2.6.0', '2.5.99') is True


def test_updater_prefers_uav_asset_and_accepts_transition_aliases():
    canonical = {
        'assets': {
            'UAV_Browser.exe': 'https://example.invalid/uav.exe',
            'ALOS_Browse.exe': 'https://example.invalid/alos.exe',
            'JableTV_Modern.exe': 'https://example.invalid/jable.exe',
        },
    }
    alos_only = {
        'assets': {
            'ALOS_Browse.exe': 'https://example.invalid/alos.exe',
            'JableTV_Modern.exe': 'https://example.invalid/jable.exe',
        },
    }
    jable_only = {
        'assets': {
            'JableTV_Modern.exe': 'https://example.invalid/jable.exe',
        },
    }

    assert updater.find_asset_url(canonical, 'browse').endswith('/uav.exe')
    assert updater.find_asset_url(alos_only, 'browse').endswith('/alos.exe')
    assert updater.find_asset_url(jable_only, 'browse').endswith('/jable.exe')


class _FakeResp:
    def __init__(self, status=200, chunks=(b'',), headers=None):
        self.status_code = status
        self._chunks = chunks
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=0):
        for c in self._chunks:
            yield c


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **k):
        return self._resp


def _patch_session(monkeypatch, resp):
    monkeypatch.setattr(updater, '_session', lambda: _FakeSession(resp))


def test_check_latest_skips_unreachable_candidate(monkeypatch):
    from types import SimpleNamespace
    calls = []

    class Session(_FakeSession):
        def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise updater.requests.ConnectionError('offline fixture')
            return SimpleNamespace(status_code=200, json=lambda: {'tag_name': 'v1.2.3'})

    monkeypatch.setattr(updater, '_session', lambda: Session(None))
    monkeypatch.setattr(updater, 'API_CANDIDATES',
                        ('https://example.invalid/a', 'https://example.invalid/b'))
    assert updater.check_latest()['version'] == '1.2.3'
    assert calls == list(updater.API_CANDIDATES)


def test_download_asset_rejects_too_small(monkeypatch, tmp_path):
    _patch_session(monkeypatch, _FakeResp(chunks=[b'MZ' + b'\x00' * 100]))
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is False
    assert not (tmp_path / 'out.exe').exists()


def test_download_asset_rejects_non_mz(monkeypatch, tmp_path):
    _patch_session(monkeypatch, _FakeResp(chunks=[b'XX' + b'\x00' * 3_000_001]))
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is False
    assert not (tmp_path / 'out.exe').exists()
    assert not (tmp_path / 'out.exe.part').exists()


@pytest.mark.parametrize('encoding,declared,wire,valid', [
    ('identity', 3_100_000, None, False),
    ('identity', 0, None, False),
    ('identity', 3_000_003, None, True),
    ('gzip', 4_000, 4_000, True),
    ('gzip', 4_000, 3_000, False),
])
def test_update_checks_wire_length_and_preserves_previous_file(
        monkeypatch, tmp_path, encoding, declared, wire, valid):
    payload = b'MZ' + b'x' * 3_000_001
    response = _FakeResp(chunks=[payload], headers={
        'content-length': str(declared), 'content-encoding': encoding,
    })
    if wire is not None:
        response.raw = io.BytesIO()
        response.raw.seek(wire)
    _patch_session(monkeypatch, response)
    dest = tmp_path / 'update.exe'
    dest.write_bytes(b'previous')
    progress = []
    assert updater.download_asset('https://example.invalid/update.exe', str(dest),
                                  lambda *args: progress.append(args)) is valid
    assert dest.read_bytes() == (payload if valid else b'previous')
    assert not (tmp_path / 'update.exe.part').exists()
    if valid and encoding != 'identity':
        assert progress[-1] == (len(payload), 0)


def test_download_asset_accepts_valid(monkeypatch, tmp_path):
    _patch_session(monkeypatch, _FakeResp(chunks=[b'MZ' + b'\x00' * 3_000_001]))
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is True
    assert (tmp_path / 'out.exe').exists()


def test_download_asset_rejects_http_error(monkeypatch, tmp_path):
    _patch_session(monkeypatch, _FakeResp(status=404, chunks=[b'']))
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is False
