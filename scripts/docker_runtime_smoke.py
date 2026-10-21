#!/usr/bin/env python3
"""Offline image runtime check; run inside a container with --network none.

No downloader mock is installed: the actual CLI and Web entry point run in the
built image. An explicitly halted, private temporary queue prevents its one
synthetic URL from executing. Docker additionally prohibits external networking.
The script prints check names only, never its generated ephemeral access token.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

READY_TIMEOUT = 60
SYNTHETIC_URL = 'https://jable.tv/videos/ci-smoke-001/'


def expect_status(response, expected):
    assert response[0] == expected, f'HTTP status mismatch: expected {expected}, got {response[0]}'
    return response


def request(base, path, token=None, body=None, headers=None):
    request_headers = {'Content-Type': 'application/json'}
    if token is not None:
        request_headers['Authorization'] = 'Bearer ' + token
    request_headers.update(headers or {})
    raw = None if body is None else json.dumps(body).encode('utf-8')
    req = Request(base + path, data=raw, headers=request_headers)
    try:
        response = build_opener(ProxyHandler({})).open(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def stop_web(process, token):
    if process.poll() is None:
        process.terminate()
    try:
        output, _ = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)
        raise AssertionError('Web entry point did not stop within 15 seconds') from None
    assert token.encode() not in output, 'Access token must not appear in service logs'
    if os.name != 'nt':
        assert process.returncode == 0, 'Web entry point did not shut down cleanly'
    return output


def start_web(environment, base):
    process = subprocess.Popen(
        [sys.executable, '-u', '-m', 'uav_downloader.web'],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    until = time.monotonic() + READY_TIMEOUT
    try:
        while time.monotonic() < until:
            if process.poll() is not None:
                raise AssertionError('Web entry point exited before becoming ready')
            try:
                expect_status(request(base, '/'), 200)
                return process
            except (OSError, URLError):
                time.sleep(.2)
        raise AssertionError('Web entry point readiness timed out')
    except BaseException:
        stop_web(process, environment['UAV_WEB_TOKEN'])
        raise


def main():
    checks = []
    cli = subprocess.run(
        [sys.executable, '-m', 'uav_downloader.cli.headless', '--help'],
        capture_output=True, timeout=30, check=True)
    assert b'--resolution' in cli.stdout and b'--max-workers-per-video' in cli.stdout
    checks.append('actual-cli-help')

    ffmpeg = shutil.which('ffmpeg')
    assert ffmpeg, 'Container must include ffmpeg'
    subprocess.run([ffmpeg, '-version'], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=30, check=True)
    import socks  # noqa: F401 - verify the documented Docker SOCKS dependency exists
    checks.append('ffmpeg-and-socks-runtime-dependencies')

    with tempfile.TemporaryDirectory(prefix='uav-image-smoke-') as temporary:
        root = Path(temporary)
        state = root / 'private-state'
        state.mkdir(mode=0o700)
        state_file = state / 'queue.json'
        state_file.write_text(json.dumps({'version': 1, 'halted': True, 'jobs': []}), encoding='utf-8')
        token = secrets.token_urlsafe(48)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        environment = dict(os.environ)
        environment.update(
            UAV_WEB_TOKEN=token, UAV_WEB_HOST='127.0.0.1', UAV_WEB_PORT=str(port),
            UAV_WEB_ALLOWED_HOSTS=f'127.0.0.1:{port}',
            UAV_WEB_STATE_DIR=str(state), DOWNLOAD_DIR=str(root / 'downloads'))
        process = start_web(environment, base)
        try:
            status, headers, body = expect_status(request(base, '/'), 200)
            assert b'UAV Downloader Web' in body and token.encode() not in body
            assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
            expect_status(request(base, '/app.js'), 200)
            expect_status(request(base, '/app.css'), 200)
            expect_status(request(base, '/api/queue'), 401)
            expect_status(request(base, '/api/queue', token + 'wrong'), 401)
            expect_status(request(base, '/api/queue', token, headers={'Origin': 'https://foreign.invalid'}), 403)
            expect_status(request(base, '/api/queue', token, headers={'Host': 'foreign.invalid'}), 403)
            snapshot = json.loads(expect_status(request(base, '/api/queue', token), 200)[2])
            assert snapshot == {'jobs': [], 'halted': True, 'worker_limit': 1}
            checks.append('actual-web-entrypoint-root-assets-auth-host-origin')

            expect_status(request(base, '/api/queue', token, {'jobs': [{'url': 'https://localhost/private'}]}), 400)
            added = json.loads(expect_status(request(base, '/api/queue', token, {
                'jobs': [{'url': SYNTHETIC_URL, 'resolution': '720',
                          'max_workers': 1, 'speed_limit_mib': .5}]}), 201)[2])
            identity = added['jobs'][0]['id']
            assert added['jobs'][0]['status'] == 'pending'
            for endpoint, expected in (('cancel', 'cancelled'), ('retry', 'pending'), ('cancel', 'cancelled')):
                snapshot = json.loads(expect_status(request(
                    base, '/api/' + endpoint, token, {'id': identity}), 200)[2])
                assert snapshot['halted'] and snapshot['jobs'][0]['status'] == expected
                assert 'running' not in snapshot['jobs'][0]['logs']
            persisted = state_file.read_bytes()
            assert token.encode() not in persisted
            assert json.loads(persisted)['jobs'][0]['status'] == 'cancelled'
            assert not list((root / 'downloads').iterdir()), 'Synthetic job must never download any files'
            checks.append('halted-offline-queue-add-cancel-retry-and-atomic-persistence')
        finally:
            output = stop_web(process, token)
            assert b'Web ready' in output

        # Exercise a real entry-point restart, not just a second in-process queue object.
        process = start_web(environment, base)
        try:
            snapshot = json.loads(expect_status(request(base, '/api/queue', token), 200)[2])
            assert snapshot['halted'] and snapshot['jobs'][0]['id'] == identity
            assert snapshot['jobs'][0]['status'] == 'cancelled'
            cleared = json.loads(expect_status(request(base, '/api/clear', token, {}), 200)[2])
            assert not cleared['jobs'] and cleared['halted']
            checks.append('actual-web-process-restart-and-clear-finished')
        finally:
            stop_web(process, token)
    print(json.dumps({'passed': True, 'checks': checks, 'external_downloads': 0}))


if __name__ == '__main__':
    main()
