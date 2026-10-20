# UAV Downloader CLI: Docker and CLI

UAV Downloader CLI downloads supported URLs without a desktop interface. It is a
run-to-completion job for Docker hosts, NAS systems, servers, and scripts.

The current container does not include a browser-based UI. The NAS Web UI
proposal is tracked in [issue #47](https://github.com/Alos21750/UAV-Downloader/issues/47).
Its initial scope is persistent URL queues, progress and logs, retry/cancel,
download settings and authenticated LAN access. No release date is committed.

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
