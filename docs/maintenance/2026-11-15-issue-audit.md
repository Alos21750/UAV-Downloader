# Maintenance review — 2026-11-15

Scope: maintain the current desktop architecture. The paused Tauri/React and
license-migration working copy is not part of this branch.

## Reviewed changes

- **PR #66:** reviewed separately from the Qt migration and merged into master,
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

[v3.5.1](https://github.com/Alos21750/UAV-Downloader/releases/tag/v3.5.1) is
published, source/tag `aec66c4d3c80dc53ea3e9c1d5fbe0c104328d16e`.
Candidate `55029f3` on `maintenance/20261115` passed Windows workflow
[43664630292](https://github.com/Alos21750/UAV-Downloader/actions/runs/43664630292),
including all 1,267 tests, a source-built LGPL FFmpeg, source/binary mapping,
system DLL imports, H.264/HEVC remux, AAC/Opus PCM extraction and Unicode paths.
The exact FFmpeg binary/source/provenance attestations were verified against
that branch and source digest. Local out-of-tree builds of Browser, Watcher and
portable Watcher also succeeded; their embedded FFmpeg and notices match the
reviewed payload byte-for-byte.

The old Gyan corresponding-source gap is not retroactively solved. The new
candidate instead uses the independently built FFmpeg and ships its exact
source/build recipe. This does not change UAV's license or resume relicensing.

The initial computer-control permission blockage was resolved after the user
restarted the desktop app. All seven local visible-window/normal-close cases
passed: Browser fresh/second/normal, Watcher fresh/second, portable fresh/second.
First-run language dialogs, retained language, Browser settings and Huangguo
categories were inspected. All exits were zero; no forced termination, downloads
or monitoring were used. Isolated profiles seed only a dead-loopback proxy for
fresh runs, not a language. User preferences and history were untouched.

Final master Windows run
[43670519225](https://github.com/Alos21750/UAV-Downloader/actions/runs/43670519225)
passed all 1,267 tests and build/media gates. The five exact CI assets and the
FFmpeg bundle subjects passed attestation checks for the source digest, master
ref, official workflow and GitHub-hosted runners. Embedded payloads/notices and
all checksums matched. All seven visible/normal-close cases were repeated on
the exact CI binaries, with zero exits and no download/monitoring actions.

Those original five assets were published without rebuilding or recompressing,
then downloaded without authentication: all five byte lengths and SHA-256
digests matched. Release notes and the PR #66 reply passed exact normalized
UTF-8 anonymous readback. Docker master run 43670519319 and tag run 43671925932
passed; anonymous GHCR `latest` and `v3.5.1` inspection confirmed amd64/arm64 and
the exact release revision. PR #66 is merged and replied; no other issue was
closed. The open issue/PR decisions above remain in effect.
