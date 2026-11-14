# Download ownership and cooperative stop

The Qt Quick desktop introduced in #64 uses the same shared download manager
in `core/task_queue.py` and Watcher worker in `core/watcher.py` as the legacy
entry points. Qt snapshots stay nonblocking, list delegates remain virtualized,
and desktop command services wait for actual worker retirement before saving.
This cumulative branch combines the rendering migration with the stop and file
ownership changes. The existing Qt layout and user actions are retained.

Browser, Watcher and CLI share a stop context. Requesting stop does not acknowledge
worker exit: download threads, executor threads and FFmpeg must retire before
cleanup, slot release or a same-URL replacement. Stopping records remain visible
as transitions; CSV recovery maps them to stable cancelled, paused or incomplete
states. Existing CLI options, v2 aliases and Tk layouts remain available.

Managed download files use `.uav` under the destination. Full task and media
identities, OS file locks and atomic manifests separate cache writers and reserve
output paths. Unknown legacy caches and existing user files are preserved; output
collisions select an identity suffix rather than overwriting. Cleanup is deferred
until the owning storage session exits. This is separate from queue/configuration
multi-instance coordination. It does not add cross-restart direct Range recovery
or the later HLS integrity protocol.

Rate changes and stop wake throttled workers. Listing producers register their
epoch before thread launch and enqueue under the manager lock. Global cancel and
clear invalidate those producers; stale batches and callbacks cannot resurrect work.

Requests and curl use one total request budget, response ownership and stop-aware
waits. Official site-specific media hooks, including Huangguo redirect/header
rules and HLS relative/query URI resolution, remain active. Native DNS/connect/TLS
and blocking filesystem calls cannot always be interrupted: the owner remains
registered and shutdown reports a retryable timeout until they return.

Subtitle HTTP requests and child processes follow the same stop boundary. Model
activation rolls back if stopped; already published user data is retained. Window
close waits and saves in the background, leaving Tk to poll and report failures.
CLI Ctrl+C preserves cache, waits for the downloader and returns 130.

Run regression and full tests with Python 3.12+ in an isolated APPDATA directory.
Windows local EXEs are review/test artifacts, not official release builds. Live
sites, NAS lock semantics, physical UI responsiveness and long-running transfers
need separate acceptance. Destination paths remain subject to Windows path limits.
