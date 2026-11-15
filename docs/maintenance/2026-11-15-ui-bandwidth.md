# UI / bandwidth development checkpoint — 2026-11-15

Status: v3.7.0 is publicly released. The original checkpoint was local-only;
completed publication evidence is recorded below.
Tauri/React and licensing work remain paused and untouched.

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

- Full release-candidate suite: **1417 passed, 1 skipped** (28.48 seconds), using Python 3.12 and
  `UAV_FFMPEG_BUNDLE=C:\jable_build\ffmpeg-ci-v360-ae16079`.
- 34 bandwidth/UI/transport regression tests include concurrent shared-budget timing, real
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

## Release evidence

- Release: https://github.com/Alos21750/UAV-Downloader/releases/tag/v3.7.0
- Exact source/tag: `9f947796b7d2de85a3d26c6436697c1dea32dd37`. Implementation
  `94bb4cd` was merged with the remote automated star-history SVG update; no
  application or packaged input changed during that merge.
- Final merged-source local tests: **1417 passed, 1 skipped** (25.55 seconds).
  Windows CI **43700446893**: **1418 passed**, successful build, provenance and
  media-operation checks. All eight artifact attestations verified against the
  exact source, master ref, official workflow and GitHub-hosted runners.
- Out-of-tree local and exact CI artifacts each passed seven actual visible
  launch/normal-close cases: Browser fresh/second/normal, Watcher fresh/second,
  portable fresh/second. All exit codes were zero; no forced termination. Isolated
  QA profiles used a dead loopback proxy and empty monitoring selections.
- CI Watcher accepted 2.5 MB/s, applied it on focus loss, and saved the exact value
  on normal close. Source-stage tests additionally verified restored values and
  Browser control synchronization. No live media download was performed.
- Evidence: `C:\jable_build\evidence-v370-local-r1` and
  `C:\jable_build\evidence-v370-ci-r1`; handoff image `watcher-second.png` in CI
  evidence. All QA windows closed normally.
- Exact CI assets: `C:\jable_build\ci-v370-r1`; FFmpeg bundle:
  `C:\jable_build\ffmpeg-ci-v370-r1`. Original attested portable ZIP was published
  unchanged. Embedded FFmpeg SHA-256:
  `0468a9a3ac5684a66952056e4a1ad78a3277fc658211c129e79b3701f5ed8758`.
- Exactly five public assets were anonymously downloaded to
  `C:\jable_build\public-v370-r1`; all byte lengths and SHA-256 values match CI
  (`public-verification.json`). UTF-8 release notes were anonymously read back
  with an exact normalized-text match; latest stable release and tag verified.
- Master Docker run **43700446900** succeeded, including both architectures'
  offline runtime checks; public GHCR `latest` matches the exact source on amd64
  and arm64. Tag run **43701768567** also succeeded; public GHCR `v3.7.0`
  was anonymously verified for both architectures and the same exact revision.
- No unrelated issue/PR comments or closures, no relicensing, and no paused
  Tauri/React migration were included.

## Limitations

- The pre-release independent review caught the transport swap in the first
  candidate. It is now fixed: limited base HLS retains curl_cffi Chrome transport
  and the same cookie jar, uses a synchronous throttled content callback rather
  than its eager streaming queue, and keeps connection/stall timeouts without a
  total deadline consumed by intentional waits. Callback cancellation uses the
  explicit curl abort sentinel and never restarts through fallback. The existing
  shared-SSL requests fallback and Huangguo's separate scoped transport remain.
  New tests exercise real local curl transfers, shared caps, cancellation,
  unlimited changes, cookie identity, timeouts and partial-failure isolation.
  No claim of full live-video E2E is made.
- This is application-level average media throttling, not an OS-wide hard network
  shaper; socket buffers, concurrent reads, and protocol overhead can cause bursts.
- Full-size widgets/popups in every language and DPI combination have not all been
  visually reverified; the specific native and frozen checks above passed.

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
