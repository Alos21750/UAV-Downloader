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
3. Set quality, filename format, worker limit, version preference, subtitle
   mode, and optional proxy. Filename format can keep the full title or only
   the text before its first whitespace.
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

## 黃果合集：所有 EP 合成一個 MP4（v3.6.0）

在 **UAV Watcher → 顯示設定** 勾選「**黃果合集：將所有 EP 合成一個 MP4**」。預設關閉，開關變更從**下次掃描**生效；僅影響 Watcher 的黃果任務，其他網站及 Browser／CLI／Web 的分集下載不變。

- 沿用黃果的**每站資料夾與基準日期**。日期仍是站方標示的作品更新日期，不是每集首播日。每部作品按 EP **數字順序**合併為一個 MP4，範圍是當次實際可取得的公開 EP 1…N，**不代表作品已完結**。
- 任一集缺號、取得失敗或下載未完成時，不會悄悄跳過並生成不完整合集。已驗證的分集會保留在輸出資料夾下的 **`.uav-huangguo` 快取**，供下次重試或新增集數時重用，因此需預留額外磁碟空間。
- 新增 EP 時會利用快取重建完整合集，驗證成功後才安全替換舊合集。如果網站暫時回傳較少集數，會保留舊合集，不以縮短版本覆蓋；未由此任務建立或已被修改的同名檔案也不會直接覆寫。
- 只支援規格相容的 **H.264／HEVC 視訊及選用 AAC 音訊**，以 **stream copy** 合併，不重新編碼。編碼設定、解析度、時間基準或音訊配置不相容時會明確報錯並保留快取，不強行拼接。

合集功能不新增中文語音辨識；將影片合成一個 MP4 不等於自動生成中文字幕。

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
