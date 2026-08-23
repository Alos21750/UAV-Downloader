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
3. Select one or more video cards.
4. Add the selection to the persistent queue or download it immediately.
5. Follow progress on the Download tab while continuing to browse.

URLs can also be pasted directly or imported from text and CSV files. Active
downloads can be stopped and placed at the front of the queue again; failed
items can be retried independently.

## Controls

- Concurrent video downloads: 1–32, with 2 as the default.
- Segment workers per video: 1–16; individual sources may enforce a lower
  limit.
- Quality preference: highest, 1080p, 720p, 480p, 360p, or lowest.
- Optional download speed limit.
- Custom HTTP, HTTPS, SOCKS4, or SOCKS5 proxy, or the enabled Windows manual
  proxy.
- Local-first Japanese, English, and Traditional Chinese AI subtitles.

Download work and subtitle generation use separate queues. A long subtitle job
therefore does not occupy a video download slot.

Hanime1's Ura Anime and short-anime category pages use a compact card layout.
UAV Browser supports that layout as well as the standard search/result cards.

## Install and run

Download `UAV_Browser.exe` from the latest GitHub Release. The release build is
portable, includes ffmpeg, and does not require a Python installation.

From a source checkout:

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
uav-browser
```

UAV Browser checks GitHub Releases in the background. It never replaces the
running executable without user confirmation.
