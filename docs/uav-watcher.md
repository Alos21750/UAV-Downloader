# UAV Watcher

UAV Watcher is the unattended automation application in UAV Downloader.
Select what you care about once; UAV Watcher can then discover new
releases, deduplicate them across categories and sites, choose the preferred
version, download them, and optionally generate subtitles without daily manual
work.

## Why it is different

Most downloaders start with a URL the user already found. UAV Watcher starts
with an intent: selected feeds, rankings, categories, tags, or makers. It keeps
that selection, scans it on schedule, and only downloads candidates that pass
the configured date and version rules.

The current registry exposes grouped targets for all five supported sites:

| Site | Selectable targets | Examples |
|---|---:|---|
| JableTV | 129 | new releases, rankings, categories, tags |
| MissAV | 102 | feeds, categories, tags, makers |
| SupJav | 10 | feeds, rankings, primary categories |
| Hanime1 | 272 | feeds, 9 genres, 6 date ranges, 8 durations, all 240 tags |
| Huangguo | 2 | AI short dramas, AI animated dramas |

Huangguo uses the site's **series update date**, not a separately verified
publication date for each episode. Unknown dates are skipped. Each episode has
its own site-scoped identity, so new episodes do not collide with previously
downloaded episodes or similarly named series. Per-site date and folder
overrides work the same way as for other sites.

## Set it up once

1. Choose the shared destination directory.
2. Set the shared baseline date, or open Per-site monitoring settings to give
   any site its own date, destination, or both. Sites without an override keep
   following the shared values. Existing configurations behave exactly as
   before.
3. Set quality, filename format, worker limit, shared download speed cap (MB/s), version preference, subtitle
   mode, and optional proxy. Filename format can keep the full title or only
   the text before its first whitespace. Enter `0` for unlimited bandwidth or `0.01–10000` MB/s; the cap applies across all active media downloads in that Watcher process.
4. Search or browse each site tab and select targets. Whole groups can be
   selected together.
5. Choose an interval from 1–168 hours, or one daily time in the computer's
   local timezone.
6. Start monitoring.

Check Now performs one immediate scan without creating a duplicate schedule.
The activity panel can remain collapsed while idle and opened only when a log
is needed.

Initial and scheduled scans have no fixed page cap. Watcher keeps scanning until
the listing ends, the baseline date is reached, a scheduled check reaches a
fully seen page, or the site repeats a listing page. Cancellation stays available
during scanning, and detail requests retain their existing pacing.

Choose **All versions** to retain every distinct version of a release (standard,
Chinese subtitles, English subtitles, uncensored, or reduced mosaic). Identical
versions still deduplicate across categories and sites. Changing the version
preference triggers a fresh scan from the baseline so previously skipped
versions can be reconsidered, while successful downloads stay recorded.
In All versions mode, files go into `versions/<version-kind>/` underneath each
site's configured destination; combined variants use a name such as
`chinese-subtitle+uncensored`. These separate folders prevent versions with the
same code-only filename from overwriting or suppressing each other.

MissAV downloads use at most 4 segment requests per video and 8 across all
active MissAV videos. If a transient CDN failure leaves a few segments missing,
UAV Watcher keeps validated chunks, leaves the run pending, and resumes only
the missing segments on the next check.

Destination storage errors (including a full disk or device I/O failure) stop
monitoring immediately. The affected video stays pending and validated chunks
are preserved. Check available space and device health, then explicitly start
monitoring again; storage failures do not enter the scheduled network-retry loop.

## Huangguo collections: combine all episodes into one MP4 (v3.6.0)

In **UAV Watcher → Show settings**, enable **Huangguo: combine all episodes into one MP4**. It is off by default, and changes take effect on the **next scan**; this applies only to Watcher's Huangguo jobs, while other sites and Browser/CLI/Web per-episode downloads remain unchanged.

- Uses Huangguo's **per-site folder and baseline date** (the site's series update date, not each episode's premiere date). Each series is combined in **numeric EP order** into one MP4 covering the publicly accessible episodes actually found (`EP 1…N`), which **does not imply the series has ended**.
- If any episode is missing, fails to resolve, or fails to download, the collection build is deferred rather than silently skipping a part. Validated episode caches are retained under `.uav-huangguo` inside the output directory for retries and future episodes, so allow extra disk space.
- When new episodes are found, the complete collection is rebuilt from cache and replaces the prior collection atomically only after verification succeeds. If the site temporarily returns fewer episodes, the existing collection is preserved rather than overwritten by a shorter file; files with the same name not created by this task or modified externally are not blindly overwritten.
- Supports compatible **H.264/HEVC video and optional AAC audio** joined via **stream copy** without re-encoding. Incompatible codecs, resolutions, time bases, or audio configurations report a clear error and retain the cache without forcing a broken merge.

This feature does not add Chinese speech recognition; combining episodes into one MP4 does not automatically generate Chinese subtitles.

## Deduplication and state

When a stable video identifier is available, UAV Watcher combines duplicates
found in multiple categories or sites and retains the candidate that best
matches the configured version preference. When an identifier cannot be
established safely, only identical URLs are deduplicated; the application does
not guess.

Portable state is stored beside the application in `.uav-watcher` when that
location is writable. Otherwise it uses
`%APPDATA%\UAV Downloader\watch`. v3 copies compatible state from
the previous locations without deleting the original data or overwriting newer
files. See the [UAV migration guide](./migration-to-uav.md).

## Install and run

- `UAV_Watcher.exe`: portable one-file Windows build.
- `UAV_Watcher_portable.zip`: onedir fallback that avoids temporary one-file
  extraction.

From source:

```bash
python -m pip install -e .
uav-watcher
```
