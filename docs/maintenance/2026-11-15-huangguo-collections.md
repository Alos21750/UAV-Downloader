# Huangguo collections — v3.6.0 verification

Release: [UAV Downloader v3.6.0](https://github.com/Alos21750/UAV-Downloader/releases/tag/v3.6.0)

Release source/tag: `ae160790044d2687a06f14379893dd8d8c8136f7`.

## Delivered behavior

Watcher has an optional, default-off Huangguo collection setting. It groups
the currently public contiguous EP 1…N list in numeric order, independently
of normal per-episode history, and uses the selected Huangguo folder/date.
Verified successful episode caches are resolution-specific and retained for
retry and additions. New episodes rebuild the complete MP4; missing episodes,
shrinking listings, failed downloads, incompatible media and modified user
outputs fail safely without replacing a valid prior collection.

Publication receipts support interrupted-state recovery. A per-series process
lock covers download, publication, stale managed-subtitle cleanup and subtitle
generation against the complete timeline. Manual/edited subtitles are preserved.
The Windows initial-publication path supports filesystems without hardlinks.

The implementation uses stream copy for compatible H.264/HEVC and optional
AAC, not transcoding. It does not infer that a series has ended, add Chinese
ASR, change Browser/CLI/Web to collection mode, resume the paused desktop
rewrite, or change the project's Apache-2.0 license.

## Verification

- Final committed-source local suite: **1,383 passed, 1 skipped** in 18.96s.
  The skip is Windows symbolic-link privilege; no test was disabled.
- Exact-source Windows CI: **1,384 passed** in 26.24s,
  [run 43675915926](https://github.com/Alos21750/UAV-Downloader/actions/runs/43675915926).
- Synthetic actual-media tests verify H.264/HEVC + AAC concatenation,
  playback/decode, order, duration, cancellation and storage behavior.
  Integrated Watcher tests exercise cache reuse, new EPs, merged subtitle
  paths, and stale subtitle cleanup with disabled or failed translation.
  No complete live-site video download is claimed.
- Independent review identified and resolved three issues: before-baseline
  collection jobs overwriting EP1 history, old subtitle sidecars surviving
  media replacement, and a process-lock gap during subtitle generation.
- Local and exact CI builds each passed **seven actual visible-window and
  normal-close cases**: Browser fresh/second/normal, Watcher onefile
  fresh/second, and Watcher portable fresh/second. All exit codes were zero;
  no forced termination, monitoring or download action was used. The actual
  collection checkbox default, enable action and restart persistence passed.
- Eight attestation subjects were checked against the exact source/master,
  official Windows workflow and GitHub-hosted runners. The five release files
  were published unchanged; the portable ZIP was not recompressed.
- All five public release files were downloaded without authentication and
  matched the verified CI byte lengths and SHA-256 values. UTF-8 release notes
  were read back anonymously and matched the reviewed text exactly.
- Docker master run `43675915970` and tag run `43677306951` succeeded,
  including offline amd64/arm64 runtime checks. Anonymous GHCR verification
  confirmed both `latest` and `v3.6.0` contain those architectures and the exact
  release-source revision. Collection mode remains Watcher-only, not a new
  Docker/Web feature.

## Published files

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| UAV_Browser.exe | 68401323 | `a1342558c51fbfce399140d05db9a31b0dfe31b541ece8a2a1ea4579d7d728d1` |
| UAV_Watcher.exe | 68243968 | `f033bfe34dfe7851c59d01cfec6f3139a2fa44c88fd409de6032bf1d1b30555f` |
| UAV_Watcher_portable.zip | 69968580 | `773a21f6f5d6436478f6d30d2de1b76ec39df4abf4d7f02d43727e6b483192df` |
| UAV_FFmpeg_7.1_Corresponding_Source.tar.xz | 11036120 | `fc130a02d045d1e1a79b92378e08d02b4734a5c40184af749f9c9ffe94b3e927` |
| UAV_SHA256SUMS.txt | 368 | `b6373e7b69ddae236f2af47450104103e4650fdfb8ca6f96b585bee1f0b7167a` |

This was a direct feature request, not an issue-closure batch. No unrelated
issue or pull request was closed or merged.
