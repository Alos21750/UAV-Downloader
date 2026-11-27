# Resume, cancellation, and upgrades

UAV Browser, Watcher, and the headless downloader keep incomplete HLS segments in a
playlist-specific folder in the selected destination. A changed source, quality, or
timeline starts a separate cache. A matching playlist may resume even if its known
authentication query parameters have been refreshed. Unknown query selectors are
part of the fingerprint. Folder names contain hashes, not media URLs or keys.

Upgrading from 3.8.5 or earlier: old numbered segments alone cannot prove which
playlist they belong to. They are left untouched and are not automatically merged
into a new download. Previously saved Browser partial-export receipts still work
when the original files match the receipt. An upgrade may therefore download
some segments again. Completed MP4s are not migrated or deleted.

Watcher Stop and Browser Pause keep recoverable segments. Browser cancellation
with cleanup removes only this transfer's segment files, after the writers retire.
A true destination I/O error still stops the queue and preserves the cache.
Files in the destination's `.uav-locks` directory are small, persistent lock files:
their presence alone does not mean a download is running. Browser and Watcher
can share a destination; if both need the same output, retry after the owner exits.

UAV cannot guarantee that a provider will serve a segment it declines to deliver.
Resume and partial export do not bypass provider access restrictions.
