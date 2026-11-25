---
name: Incomplete download / 分段下載未完成
about: Report a source refusal, rate limit or temporary media failure without sharing secrets.
title: ""
---

**No full-batch retest, network change or cache deletion is needed.** 請使用已有的錯誤訊息，不必重抓整批、改網路或刪除快取。

- UAV version and application / 版本及工具（Browser、Watcher、CLI）：
- Provider and stage / 網站與失敗階段（listing 清單 / playback info 解析 / segments 片段 / merge 合併）：
- Status and counts, if shown / 已顯示的狀態與數量（HTTP 狀態、已拒絕幾段、未請求幾段；沒有就留白）：
- Expected and actual behavior / 預期與實際結果：
- Optional: if this happened in normal use, did the job retain its progress? / 選填：正常使用時是否有保留進度？

A segment 403 means that request was refused, not that the entire site or your IP is blocked. A 429/503 with Retry-After requests a pause. 片段被拒和來源要求等待是兩種不同情況。

Do not paste raw copied diagnostics, signed media/playlist URLs, cookies, tokens, HAR files, proxy credentials, or local paths. 如需附圖，請先遮住使用者目錄、私人網址等資訊。
