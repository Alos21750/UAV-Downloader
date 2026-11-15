# Maintenance review — 2026-11-15

Scope: maintain the current desktop architecture. The paused Tauri/React and
license-migration working copy is not part of this branch.

## Reviewed changes

- **PR #66:** reviewed separately from the Qt migration and merged locally,
  preserving the contributor commit. Hanime1 can recover a missing title from
  its matching download page without discarding an already usable media source.
  Independent regression tests also require metadata request failures to
  propagate when there is no usable source.
- **Watcher source errors:** an adapter failing to resolve a source is no longer
  reported as `invalid URL` or persisted as a skipped download. Source failure
  leaves the scan incomplete. Existing reasonless `skipped: true` records are
  reconsidered during scanning without deleting history; successful entries and
  explicit date/version exclusions retain their existing semantics. Storage
  errors still halt monitoring. New source diagnostics do not echo exception
  strings containing signed media URLs.

- **Huangguo playback compatibility:** live inspection confirmed that the new
  public player obtains a source through its anonymous JSON playback endpoint,
  while server-rendered `videoSrc` is empty. The adapter now supports this flow
  with identity/method/source validation, a bounded response, no redirects or
  imported login credentials, and no persistent source/header cache. Three
  independently sampled series resolved and parsed manifests containing
  44/42/74 MPEG-TS segments. No media segments, previews, keys or complete videos
  were downloaded. This does not establish universal site access or full-video
  completion.
  A second cross-check covered both AI short dramas and AI cartoons, including
  a non-root episode URL: 2/3/3 actual episode links, successful source and
  manifest resolution with 44/50/90 MPEG-TS segments. Still no media download.

## GitHub triage

| Item | Decision |
| --- | --- |
| #62 | Keep open. Screenshots show a pre-Python DLL-load failure and a separate in-app network/proxy failure. Requested exact asset, Windows details, hash/security evidence and proxy mode. Existing bundle evidence does not establish a missing runtime DLL. |
| #63 | Already closed by reporter; empty body, no action. |
| #64 | Keep open; do not merge Qt Quick migration in this maintenance scope. Requested independent core fixes against official master. |
| #65 | Keep draft/open; depends on #64. Requested standalone cancellation/ownership fixes with targeted regressions rather than accepting cumulative changes. |
| #54 / #55 / #57 / #59 | Retain existing open status and prior evidence requests/feature boundaries; no duplicate replies or unsupported closure claims. |

Replies to #62/#64/#65 were published as UTF-8 files and anonymously read back
with exact normalized-text equality.

## Publication status

No new executable release has been published by this maintenance review.
Frozen-build verification and public asset verification remain required before
any release claim. A separate packaging audit identified an unresolved source
and notice mapping for the bundled Gyan FFmpeg binary; adding notices alone is
not evidence that the matching complete source/build inputs are available.
This does not change UAV's current license or resume the paused relicensing work.
