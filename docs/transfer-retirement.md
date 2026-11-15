# Transfer retirement before cleanup and retry

Cancellation requests no longer discard executor handles or remove a cache
while its admitted transfer is still using it. Crawler entry points reserve a
transfer lifetime, including admission before background dispatch. A queued
future cancelled before execution releases its reservation. `wait_stopped`
waits for admitted calls, cleanup and actual executor threads; a done Future
alone is insufficient. A storage error revokes a prior cleanup request.
Cleanup is idempotent for a stopped lifetime, including repeated cancellation.
Late executor storage errors are checked again after joining. Waiting from the
owning call, executor callback or cleanup thread returns False rather than
deadlocking that same lifetime. Retirement also gates new admission.

HLS, SupJav/Hanime1 direct transfers and provider fallback retain the crawler's
lifetime. Remux and official 3.6.0 collection FFmpeg processes remain owned until reaped, even if killing or
waiting initially fails. A stopped merge/move does not start or remove an
existing output. These are stop checks, not an atomic publication transaction.

The original Tk download manager requests cancellation without repeating a
crawler's cleanup, and retains its slot through retirement before replacement,
transient retry or subtitle handoff. Browser close waits/saves off the Tk thread,
rejects new admission, preserves resumable rows and historical paused records,
and allows an explicit close retry after save failure without logging the error
body. Queue locks are released before filesystem saving, and shutdown rejects
delayed worker starts. Watcher close is also idempotent. It delegates cache cleanup to the crawler and polls for worker exit
when closing. Both CLI entry paths preserve cache and wait after Ctrl+C during
an admitted transfer, returning 130. Network parsing before a crawler is
returned is outside this lifetime contract.

Tk layouts, settings, queue CSV/Watcher history schemas, site transport hooks,
public CLI flags, v2 aliases, dependencies and Windows specs are retained.
`tests/test_transfer_retirement.py` covers controlled I/O, executor reservations,
failed child disposal, a real child process, retry ordering, storage failure,
close/save and the existing Watcher/CLI paths. A selected baseline comparison
has ten behavioral failures and one passing cache-preservation control on
official master dc1c085; all eleven pass with this change. Official Huangguo
collection downloads, subtitle retry and history behavior remain covered by
the upstream collection regression suites.

This candidate is independent of the rate-limit fix in PR #67. A cumulative
local Tk trial build can include both, with its separate integration SHA recorded.
Without #67, blocked rate-limit waits retain the official baseline limitation.
Neither change forcibly interrupts native DNS/TLS, network reads or filesystem
operations. Until they return, shutdown retains ownership and the Tk window.

Cross-process/same-media cache locks, output-name collision handling, unknown
legacy-cache ownership and an atomic cancel/publication boundary remain the
separate storage-ownership task. This change does not claim they are solved.
Local tests/builds and user feedback do not replace maintainer validation.
