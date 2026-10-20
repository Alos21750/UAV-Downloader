# UAV Downloader CLI: Docker and CLI

UAV Downloader CLI downloads supported URLs without a desktop interface. It is a
run-to-completion job for Docker hosts, NAS systems, servers, and scripts.

An opt-in **UAV Downloader Web** queue is included for NAS hosts
([issue #47](https://github.com/Alos21750/UAV-Downloader/issues/47)). The default
container remains the run-to-completion CLI. The Web queue is a deliberately
small remote downloader, **not** the full desktop Browser or Watcher.

## NAS Web queue (opt-in)

Features: persistent URL queue, live segment/byte progress and event logs,
retry/cancel, per-job quality, 1–16 segment workers and a speed cap in MiB/s.
There is exactly **one active video**. Download destination is the administrator's
mounted `/downloads`; users cannot choose arbitrary server paths or browse files.
Only maintained JableTV/MissAV/SupJav/Hanime1 HTTPS video URLs are accepted.
Site blocking and unavailable provider streams still apply; this does not bypass
Cloudflare or add support for arbitrary media URLs.

Generate a **new random secret** (at least 32 URL-safe characters), put it in
`UAV_WEB_TOKEN`, then enable the Compose profile. Do not reuse an API credential.
For example in a private shell, generate and export without printing the value:

```bash
export UAV_WEB_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose --profile web up -d uav-web
```

Open `http://127.0.0.1:8765` and paste the same token from your private secret
store. The example above intentionally does not print it; save it in your secret
manager before closing the shell. On a NAS, configure the variable in its private
container settings and store the same value securely. An empty/weak token causes
startup to fail before a listener is opened. The token gates **every API call**,
including queue reads, and is never written to queue state or rendered into HTML.
The UI keeps it in memory only (no browser storage, cookie or URL); reload or
Disconnect forgets it. Do not place tokens in query strings or share screenshots
of private container settings. Docker administrators can read environment values;
this is not isolation from the host administrator.

The published port is loopback-only by default. For another device, use your
private VPN or an authenticated **TLS** reverse proxy. Never expose this Python
HTTP server directly to the public Internet, and never transmit its Bearer token
over an untrusted plain-HTTP LAN. If the browser uses a different hostname, set
`UAV_WEB_ALLOWED_HOSTS` to exact `host:port` values, separated by commas (for
HTTPS on port 443, browsers normally send just the hostname). Preserve that Host
in your reverse proxy; no wildcard hosts, permissive CORS or forwarded-host trust
are enabled. WebSocket/SSE proxy support is not needed (polling every two seconds).

Standalone mode, after installing the normal downloader dependencies:

```bash
DOWNLOAD_DIR=/path/to/videos UAV_WEB_STATE_DIR=/path/to/private-state \
  python -m uav_downloader.web
```

`UAV_WEB_HOST` defaults to `127.0.0.1`, `UAV_WEB_PORT` to `8765`.
Compose binds inside the container to `0.0.0.0` while publishing only loopback.
The `/state` named volume holds atomic queue metadata; keep it private and back it
up. It contains source URLs and settings, not the access token. Do not share one
state directory between multiple running servers. A corrupt state file stops
startup without overwriting it. Jobs that were running when the server stopped
become **interrupted** and need explicit Retry; untouched pending jobs remain
queued. Cancellation is cooperative: resolving or an in-flight network request
may need its existing timeout before the single worker is free. Valid downloaded
segments are preserved for a manual retry; no guarantee is made that every
provider supports partial resumption.

Destination or state I/O failure halts the queue, rather than automatically
hammering an unhealthy disk. Check disk health/free space first, then use
**Disk checked — resume queue** and explicitly Retry the failed job. This cannot
repair failing hardware. The queue is bounded to 1,000 records (100 per request);
Clear finished removes metadata only, never your downloaded files.

Current boundaries: no automatic Watcher monitoring, browsing/search, thumbnail
gallery, AI subtitles, per-site destination picker, schedule, multi-user accounts
or file-serving endpoint. Use UAV Browser/Watcher for those desktop workflows.
The interface supports Traditional Chinese and English.

## Docker

The canonical multi-architecture image is:

```text
ghcr.io/alos21750/uav-downloader:latest
```

GitHub Actions publishes Linux amd64 and arm64 manifests. The former
`ghcr.io/alos21750/alos-unified-av-downloader` and
`ghcr.io/alos21750/jabletv` image names remain compatibility channels.

Download one URL:

```bash
docker run --rm \
  -v "/path/to/downloads:/downloads" \
  ghcr.io/alos21750/uav-downloader:latest \
  "https://jable.tv/videos/example/"
```

With Compose:

```bash
docker compose run --rm uav "https://jable.tv/videos/example/"
```

To process a list, write one URL per line to `./downloads/urls.txt`, then run:

```bash
docker compose run --rm uav
```

Supported environment variables:

| Variable | Purpose |
|---|---|
| `DOWNLOAD_DIR` | Container destination; default `/downloads` |
| `URL` / `URLS` | One or more URLs |
| `URLS_FILE` | Mounted URL list; default `/downloads/urls.txt` |
| `RESOLUTION` | `highest`, `1080`, `720`, `480`, `360`, or `lowest` |
| `MAX_WORKERS_PER_VIDEO` | Segment-worker limit from 1–16 |

## Installed commands

After an editable or package installation:

```bash
uav-downloader-cli --output ./download "https://jable.tv/videos/example/"
uav-browser --nogui --url "https://jable.tv/videos/example/" -o ./download
```

The first command follows the Docker-style URL and environment interface. The
second exposes the classic arguments shared with UAV Browser. Run
`uav-downloader-cli --help` for resolution and worker-limit options. A local
CLI run defaults to `./download`; the container still defaults to `/downloads`
through its `DOWNLOAD_DIR` environment setting.
