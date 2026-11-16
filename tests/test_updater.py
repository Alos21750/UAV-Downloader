# coding: utf-8
"""Auto-updater tests: version compare (drives whether users are offered updates) and
download_asset validation (size floor + MZ-header check + atomic replace)."""
from uav_downloader.core import updater


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


def test_download_asset_accepts_valid(monkeypatch, tmp_path):
    _patch_session(monkeypatch, _FakeResp(chunks=[b'MZ' + b'\x00' * 3_000_001]))
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is True
    assert (tmp_path / 'out.exe').exists()


def test_download_asset_rejects_http_error(monkeypatch, tmp_path):
    _patch_session(monkeypatch, _FakeResp(status=404, chunks=[b'']))
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is False


def test_download_asset_rejects_truncated_content_length(monkeypatch, tmp_path):
    _patch_session(
        monkeypatch,
        _FakeResp(
            chunks=[b'MZ' + b'\x00' * 3_000_010],
            headers={'content-length': '5000000'},
        ),
    )
    dest = str(tmp_path / 'out.exe')
    assert updater.download_asset('http://x/app.exe', dest) is False
    assert not (tmp_path / 'out.exe').exists()


def test_check_latest_falls_back_after_candidate_error(monkeypatch):
    class _Response:
        status_code = 200

        def json(self):
            return {
                'tag_name': 'v3.7.1',
                'html_url': 'https://example.invalid/releases/v3.7.1',
                'body': 'notes',
                'assets': [],
            }

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **_kwargs):
            if url == updater.API_CANDIDATES[0]:
                raise ConnectionError('primary failed')
            return _Response()

    monkeypatch.setattr(updater, 'API_CANDIDATES', ('https://bad.invalid/latest', 'https://good.invalid/latest'))
    monkeypatch.setattr(updater, '_session', lambda: _Session())
    info = updater.check_latest()
    assert info is not None
    assert info['version'] == '3.7.1'
