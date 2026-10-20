# UAV Browser

UAV Browser is the interactive Windows application in UAV Downloader. It is
designed for people who want to search, inspect, select, and
queue individual videos from JableTV, MissAV, SupJav, and Hanime1.

## Typical workflow

1. Open the Browse tab and select a supported site.
2. Choose a category or enter a search query.
   On Hanime1, Full filters can combine keywords, 9 genres, 9 sort modes,
   6 published-date ranges, 8 duration ranges, and multiple selections from
   the complete 240-tag catalog.
3. Select the current page, select cards across pages, or select every unique
   result from the current search/listing.
4. Review the selection, remove unwanted cards, and choose a resolution and
   filename template for the batch.
5. Add the selection to the persistent queue or download it immediately. The
   result summary reports new, duplicate, and unsupported URLs separately.
6. Follow progress on the Download tab while continuing to browse.

URLs can also be pasted explicitly or imported from UTF-8 text, CSV, and JSON
files. Copying a URL only fills the input; it no longer changes the persistent
queue without a user action. Queue/history snapshots can be exported as CSV or
JSON.

The Download tab can filter All, Active, Queued, Failed, History, and Paused
items. Each row shows its source, video code, resolution, optional thumbnail,
progress, speed, and ETA. Clicking the detail line copies structured diagnostic
data. Active and queued jobs can be paused without deleting validated temporary
chunks, then resumed later. Cancel and Clear remain destructive and require
confirmation.

## Controls

### Source-player preview

Each video card has **Preview ↗** beside Select. It opens the original source
page and its full player in the default system browser, without starting a
download or changing the queue/selection. It uses that browser's own session
and proxy settings, not the app's proxy or clearance settings. Site access,
availability and playback controls remain the provider's responsibility.
This is not a Tk-embedded video player or a short trailer. The separate request
for playback embedded inside UAV Browser remains tracked in issue #55.

### Download settings

- Concurrent video downloads: 1–32, with 2 as the default.
- Segment workers per video: 1–16; individual sources may enforce a lower
  limit. MissAV uses at most 4 per video and 8 across all active MissAV videos,
  with additional tail retries for transient CDN failures.
- Quality preference: highest, 1080p, 720p, 480p, 360p, or lowest.
- Filename format: Code + full title (default), or Code only. Code only keeps
  everything before the first Unicode whitespace and therefore also supports
  names such as `FC2-PPV-1234567` without studio-specific regular expressions.
- Optional filename templates with the allowlisted `{title}`, `{code}`, and
  `{site}` fields, including at least `{title}` or `{code}` to keep identity.
  Path separators, field traversal and unsafe filename characters are rejected.
- Per-batch resolution and filename settings from the selection review dialog.
- Optional download speed limit.
- Optional daily local start time, destination free-space threshold, and
  completion notification.
- Bounded automatic retry with exponential backoff for temporary network and
  Cloudflare failures. Permanent URL, storage, permission, missing-video, and
  missing-ffmpeg failures are not retried blindly.
- Custom HTTP, HTTPS, SOCKS4, or SOCKS5 proxy, or the enabled Windows manual
  proxy.
- Local-first Japanese, English, and Traditional Chinese AI subtitles.

Download work and subtitle generation use separate queues. A long subtitle job
therefore does not occupy a video download slot.

The Download tab's Subtitle Local Files button queues existing videos using the
language settings selected in Settings. Those rows do not download or enter the
saved URL queue; Retry regenerates subtitles on the same local file.

Slow-download requeue is optional and defaults to 0 (off). Set a KB/s threshold
in Settings to inspect successive 60-second transfer windows, including stalls.
UAV Browser restarts a slow transfer up to three times, then warns and continues.
A restart discards transfer progress to avoid mixing different source streams.
Preparation, remux, completed transfers and subtitles are excluded. A global
speed cap disables automatic requeue.

Destination I/O failures stop the download/subtitle queue without deleting
validated chunks. Check free space and device health before explicitly retrying.
UAV Browser also performs a writable-directory and free-space preflight before
starting a new batch.

The queue file now keeps a stable job ID, site, thumbnail, resolution, filename
template, error category, retry count, and timestamps. Older queue CSV files
remain readable; interrupted active/scheduled states are restored as Incomplete
so they never start unexpectedly after a crash.

Thai is available from the language picker for the primary browse, selection,
download, queue, safety, and settings workflow. Site-provided category names
fall back to English when a reviewed Thai label is unavailable.

Validated HLS segments are resumable. If a MissAV CDN request remains
unavailable after the automatic retry window, Retry requests only the missing
segments rather than restarting the complete video.

Hanime1's Ura Anime and short-anime category pages use a compact card layout.
UAV Browser supports that layout as well as the standard search/result cards.

## Install and run

Download `UAV_Browser.exe` from the latest GitHub Release. The release build is
portable, includes ffmpeg, and does not require a Python installation.

From a source checkout:

```bash
python -m pip install -e .
uav-browser
```

UAV Browser checks GitHub Releases in the background. It never replaces the
running executable without user confirmation.
