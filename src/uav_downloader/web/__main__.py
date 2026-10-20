"""Run with ``python -m uav_downloader.web``; opt-in, never the CLI default."""
import os
import signal
import threading

from .queue import QueueManager
from .server import make_server, validate_token


def main():
    # Validate the secret before creating state or opening a listener.
    token = validate_token(os.environ.get('UAV_WEB_TOKEN'))
    host = os.environ.get('UAV_WEB_HOST', '127.0.0.1')
    port = int(os.environ.get('UAV_WEB_PORT', '8765'))
    if not 1 <= port <= 65535:
        raise ValueError('UAV_WEB_PORT must be 1..65535')
    allowed = os.environ.get('UAV_WEB_ALLOWED_HOSTS', '')
    hosts = {item.strip().lower() for item in allowed.split(',') if item.strip()} or None
    queue = QueueManager(os.environ.get('DOWNLOAD_DIR', '/downloads'),
                         os.environ.get('UAV_WEB_STATE_DIR', '/state'), autostart=False)
    try:
        server = make_server(host, port, token, queue, hosts)
        def shutdown(*_args):
            threading.Thread(target=server.shutdown, daemon=True).start()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, shutdown)
        queue.start()
        print('UAV Downloader Web ready. Authentication required; keep behind VPN/TLS.', flush=True)
        try:
            server.serve_forever(poll_interval=.25)
        finally:
            server.server_close()
    finally:
        queue.close()


if __name__ == '__main__':
    main()
