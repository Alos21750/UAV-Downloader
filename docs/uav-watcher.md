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

The current registry exposes grouped targets for all four supported sites:

| Site | Selectable targets | Examples |
|---|---:|---|
| JableTV | 129 | new releases, rankings, categories, tags |
| MissAV | 102 | feeds, categories, tags, makers |
| SupJav | 10 | feeds, rankings, primary categories |
| Hanime1 | 272 | feeds, 9 genres, 6 date ranges, 8 durations, all 240 tags |

## Set it up once

1. Choose the shared destination directory.
2. Set the shared baseline date, or open Per-site monitoring settings to give
   any site its own date, destination, or both. Sites without an override keep
   following the shared values. Existing configurations behave exactly as
   before.
3. Set quality, worker limit, version preference, subtitle mode, and optional
   proxy.
4. Search or browse each site tab and select targets. Whole groups can be
   selected together.
5. Choose an interval from 1–168 hours, or one daily time in the computer's
   local timezone.
6. Start monitoring.

Check Now performs one immediate scan without creating a duplicate schedule.
The activity panel can remain collapsed while idle and opened only when a log
is needed.

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
python -m pip install -r requirements.txt
python -m pip install -e .
uav-watcher
```
