"""Small same-origin authenticated HTTP surface, deliberately not a file server."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import urlsplit

from uav_downloader.core.storage import StorageWriteError
from .ui import HTML, CSS, JS

MAX_BODY = 256 * 1024


def validate_token(value):
    # A length check cannot prove randomness; documentation mandates secrets.token_urlsafe.
    if (not isinstance(value, str) or not 32 <= len(value) <= 256
            or len(set(value)) < 12 or not re.fullmatch(r'[A-Za-z0-9_-]+', value)):
        raise ValueError('UAV_WEB_TOKEN must be a random URL-safe token of at least 32 characters')
    return value


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_server(host, port, token, queue, allowed_hosts=None):
    digest = hashlib.sha256(validate_token(token).encode()).digest()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(8)

        def log_message(self, *_args):
            pass  # Never log Authorization, attacker-controlled paths or token query strings.

        def _send(self, status, payload, kind='application/json; charset=utf-8'):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=True).encode()
            elif isinstance(payload, str):
                payload = payload.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'none'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'; object-src 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def _guard(self):
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1 or hosts[0].lower() not in self.server.allowed_hosts:
                self._send(403, {'error': 'Host is not allowed'})
                return False
            if '?' in self.path or '#' in self.path:
                self._send(400, {'error': 'Query parameters are not accepted'})
                return False
            origin = self.headers.get('Origin')
            if origin:
                try:
                    parsed = urlsplit(origin)
                    valid = (parsed.scheme in {'http', 'https'} and parsed.netloc.lower() == hosts[0].lower()
                             and not parsed.path and not parsed.query and not parsed.fragment)
                except ValueError:
                    valid = False
                if not valid:
                    self._send(403, {'error': 'Cross-origin request denied'})
                    return False
            return True

        def _auth(self):
            values = self.headers.get_all('Authorization', [])
            value = values[0] if len(values) == 1 else ''
            supplied = value[7:] if value.startswith('Bearer ') else ''
            if not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(), digest):
                self._send(401, {'error': 'Authentication required'})
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            static = {'/': (HTML, 'text/html; charset=utf-8'),
                      '/app.css': (CSS, 'text/css; charset=utf-8'),
                      '/app.js': (JS, 'text/javascript; charset=utf-8')}
            if self.path in static:
                self._send(200, *static[self.path])
            elif self.path == '/api/queue' and self._auth():
                self._send(200, queue.snapshot())
            elif self.path != '/api/queue':
                self._send(404, {'error': 'Not found'})

        def do_POST(self):
            if not self._guard() or not self._auth():
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
                    raise ValueError('Content-Length is required; chunked bodies are not accepted')
                size = int(lengths[0])
                if not 0 < size <= MAX_BODY:
                    self._send(413, {'error': 'Request is too large or empty'})
                    return
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('Use application/json')
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('Incomplete request body')
                data = json.loads(raw.decode('utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('Expected an object')
                if self.path == '/api/queue':
                    if set(data) != {'jobs'}:
                        raise ValueError('Expected only jobs')
                    self._send(201, {'jobs': queue.add(data['jobs'])})
                    return
                if self.path in {'/api/resume', '/api/clear'}:
                    if data:
                        raise ValueError('Expected an empty object')
                    (queue.resume if self.path == '/api/resume' else queue.clear)()
                elif self.path in {'/api/cancel', '/api/retry'}:
                    if set(data) != {'id'} or not isinstance(data['id'], str):
                        raise ValueError('Expected only id')
                    (queue.cancel if self.path == '/api/cancel' else queue.retry)(data['id'])
                else:
                    self._send(404, {'error': 'Not found'})
                    return
                self._send(200, queue.snapshot())
            except KeyError:
                self._send(404, {'error': 'Job not found'})
            except StorageWriteError:
                self._send(503, {'error': 'Storage failed; queue halted. Check disk health before resuming.'})
            except (ValueError, UnicodeError, TypeError, RecursionError):
                self._send(400, {'error': 'Invalid request or unsupported URL/settings'})
            except OSError:
                self._send(503, {'error': 'I/O unavailable'})

    server = BoundedServer((host, port), Handler)
    actual_port = server.server_port
    defaults = {f'localhost:{actual_port}', f'127.0.0.1:{actual_port}'}
    if host not in {'0.0.0.0', '::', ''}:
        defaults.add(f'{host.lower()}:{actual_port}')
    server.allowed_hosts = {value.lower() for value in (allowed_hosts or defaults)}
    return server
