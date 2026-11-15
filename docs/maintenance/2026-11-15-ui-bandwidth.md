# UI / bandwidth development checkpoint — 2026-11-15

Status: locally tested source candidate, **not a published release**. The public
release remains v3.6.0. Tauri/React and licensing work remain paused and untouched.

## Implemented

- Browser and Watcher share an editable bandwidth control: 0 means unlimited;
  fractional values from 0.01 through 10000 MB/s are accepted. The existing unit
  convention is retained: MB/s represents 1024² bytes per second.
- Watcher exposes the cap next to the destination on its main screen and applies
  the saved cap before monitoring can auto-start. Browser exposes the same input
  in its download toolbar and settings; the two controls synchronize immediately.
- Enter, focus loss, language rebuild, and normal window close commit edits.
  Invalid input retains the prior cap instead of silently enabling unlimited speed.
- A process-wide, monotonic shared token bucket limits all video workers in that
  process. Reapplying the same cap does not refill it. Changing to unlimited wakes
  waiters; cancellation is checked during waits. Separate running app instances
  and AI model downloads have separate bandwidth, clearly stated in settings.
- Limited HLS segments are read in chunks rather than eagerly buffered before
  throttling. SupJav direct-download waits also honor cancellation. Media responses
  are closed on all segment exit paths; cleanup errors do not replace job results.
- Watcher settings use padded monitoring/download/subtitle cards with a two-column
  wide layout and a one-column layout below 1120 logical pixels. The scroll viewport
  uses available window height and no longer inherits the scrollbar's 200px minimum
  that clipped the action row in short windows.
- Browser and Watcher small body/caption fonts were increased; secondary text has
  higher contrast. Settings descriptions align with their controls.
- Common queue/selection controls are localized in all five supported languages.
  Empty queues explain how to add URLs or select videos. Unchanged status text and
  unchanged Watcher layout sizes no longer trigger unnecessary widget updates.

## Verified

- Full suite: **1407 passed, 1 skipped** (20.31 seconds), using Python 3.12 and
  `UAV_FFMPEG_BUNDLE=C:\jable_build\ffmpeg-ci-v360-ae16079`.
- 24 bandwidth/UI regression tests include concurrent shared-budget timing, real
  local HTTP segment downloads, cancellation, unlimited wake-up, invalid input,
  control synchronization, locale coverage, and responsive layout no-op behavior.
- Isolated source UI profiles were used; a dead loopback proxy prevented loading
  external thumbnails. No user's monitoring targets or preferences were changed.
- Native visible checks: Watcher 1180×820 dark/light, 920×680 single-column, and
  760×440 minimum-size settings with the full bottom action row; Browser 1180×820
  and 980×680 download/settings pages.
- Watcher: typed 2.5 MB/s, closed without Enter, verified saved preference and
  restored value on relaunch. Browser: typed 1.5 MB/s in the toolbar, applied it,
  and visibly verified the settings control synchronized.
- Preview windows were closed normally, with process exit code 0.

Evidence is local to `C:\jable_build\ui-refresh-20261115\evidence`.
The isolated source preview launcher is `C:\jable_build\preview_uav_ui_20261115.py`.

## Remaining release gates / limitations

- No version bump, Windows package build, CI push, tag, asset upload, or public
  release verification has been performed for this candidate.
- Limited base HLS transport uses synchronous `requests` with the existing shared
  SSL adapter because curl_cffi's streaming queue can prefetch beyond a reader-side
  cap. Representative real-CDN compatibility still needs verification, especially
  sites that depend on curl_cffi's transport fingerprint. Do not treat the local
  HTTP integration test as that live compatibility gate.
- This is application-level average media throttling, not an OS-wide hard network
  shaper; socket buffers, concurrent reads, and protocol overhead can cause bursts.
- Full-size widgets/popups in every language and DPI combination have not all been
  visually reverified. Full frozen-app and release checks remain mandatory.

## User's collection-format question

The v3.6.0 message `Episode codecs, resolution or audio settings differ;
stream-copy collection is unsafe` comes from the pre-concatenation comparison in
`core/media_concat.py`. It compares video codec configuration, dimensions, stream
time bases, display/color metadata and matrix, plus AAC configuration/channels/
sample rate. Bitrate alone is intentionally ignored. Matching visible resolutions
do not guarantee matching signatures.

This is an expected safety stop, not proof of corrupt downloads. There is currently
no automatic transcoding fallback. Original episodes remain untouched. Retrying
unchanged inputs will not solve incompatible signatures. A future opt-in compatible
merge mode should normalize streams by transcoding, with clear time/CPU/disk costs;
the safety check must not simply be removed. No collection implementation was
changed in response to the diagnostic question.
