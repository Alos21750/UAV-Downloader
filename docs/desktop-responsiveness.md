# Desktop rendering and responsiveness

Browser and Watcher now start the shared Qt Quick workspace with their existing
desktop layouts. The QML pages use the original theme colors and expose the
existing controls and confirmation steps. Virtualized
`GridView` / `ListView` delegates replace the desktop's full widget trees.
Progress changes use immutable snapshots and role-specific model notifications;
surviving rows keep their identity and scroll position. Language and appearance
changes update bindings without rebuilding the window. The Qt scene graph uses
the platform's normal render loop and presentation defaults; no setting disables
vertical synchronization or forces a special Windows window style.

`TaskService` and `WatcherService` serialize commands on background workers.
Queue persistence, preference transactions, browsing, thumbnail requests, updates and potentially
slow cancellation do not execute in GUI callbacks. A queue snapshot never waits
for a busy backend lock: one poll can reuse the last complete immutable view.
Shutdown polls Futures from every service, saves recoverable queue states, and
reports failed writes or unfinished work with explicit retry/discard choices.

The existing download and subtitle manager and Watcher worker are extracted
from the Tk application modules. Their network behavior and internal CSV schema
remain the upstream versions. Legacy Tk modules re-export these types for
existing callers. Browser and Watcher continue to use separate queues and the
existing preference, portable-state and v2 migration paths. The headless CLI
does not import a GUI framework.

This port deliberately does not change site parsers, downloader recovery,
output ownership, task outcome persistence, persistence limits or subtitle
inference. The download list retains its original ordering, filters and
200-row display limit. Queue persistence is independent of the visible rows.

## Validation

Install the normal development dependencies and run `python -m pytest tests -q`.
Regression coverage includes stable delegates, batched progress notifications,
selection preservation, pending/failed saves, queue separation, background
commands and snapshots under lock contention. Windows specifications collect
QML/icons without relying on an editable installation and retain one-file and
portable Watcher builds. Qt runtime license texts ship with the artifacts.

For visual acceptance, compare Browser and Watcher against the current custom
Qt build on the same machine. Exercise 200/1000 queued rows with continuous
progress, hidden tabs, scrolling, window drag/resize, maximize/restore, themes,
languages and monitor/DPI changes. Measure without taking screenshots;
`grabWindow()` adds timing stalls. Record screenshots in a separate run.
Event-loop timing and window messages establish application behavior, while
physical display tearing still requires observation on the affected screen.
One-off measurement/capture tools and local binaries belong outside Git.
