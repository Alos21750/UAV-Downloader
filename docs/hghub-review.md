# HGhub review and UAV policy

Reviewed 2026-11-10 against [HGhub commit ee12e496](https://github.com/Minireel/HGhub/tree/ee12e496cee4dd92912842a2a238e9861ac82d5c). This is a design comparison, not an endorsement or affiliation.

## Design lessons implemented independently

HGhub's documented emphasis on reducing upstream requests, bounded media caches and clear diagnostics informed this review. UAV uses its own public-page adapter and download core; HGhub implementation code, private API protocols, player decryption and assets were not imported. Concurrent request sharing and cancellation are UAV-specific improvements identified during this review, not claims about HGhub features.

| Area | UAV implementation |
| --- | --- |
| Repeated episode discovery | Parsed public metadata only; 120-second TTL, at most 128 series / 4,000 episodes / 2 MiB serialized metadata. Returned rows are copied. The listing's update marker invalidates stale entries; proxy configurations have separate cache scopes. |
| Concurrent scans | Share an in-flight request for the same series/revision; cancellation or deadline expiry of its owner does not cancel a healthy waiting caller. |
| Cancellation | Browser page generations, listing/select-all scans and Watcher stop reach metadata operations. Cooperative checks occur between requests, body chunks and retry waits; pending sockets can take their bounded timeout. |
| Temporary failures | At most three metadata request attempts for selected transient errors. Respect Retry-After; long cooldowns are surfaced rather than shortened. Explicit challenges are not repeatedly retried. |
| UI work | Watcher creates category controls only for visited sites, in small event-loop batches. All selection variables exist immediately, preserving hidden-site settings. Filtering is debounced. |
| Shared HLS core | Resolve query-only children against the full final manifest URL, not just its directory. New child paths do not inherit the parent's signed query. |

Cache bounds describe serialized metadata, not total Python process memory. Caches are process-local and do not persist to disk. Every new playback job still resolves fresh source/header metadata. This is not seamless mid-transfer token renewal.

Existing limitations remain: public sources only, series-update dates rather than per-episode premieres, MPEG-TS HLS rather than fMP4/DASH, placeholder encrypted thumbnails, and Japanese-focused ASR. No login/paywall/challenge bypass or private-player protocol is added.

## License decision

HGhub's pinned [LICENSE](https://github.com/Minireel/HGhub/blob/ee12e496cee4dd92912842a2a238e9861ac82d5c/LICENSE) is **PolyForm Noncommercial 1.0.0**. It restricts permitted use and distribution to its terms; it is not interchangeable with MIT or Apache-2.0. HGhub's README describes earlier MIT versions, but this review does not rely on an unverified historical license.

**UAV remains [Apache-2.0](../LICENSE).** No noncommercial restriction has been silently added. Apache permits commercial use subject to its terms; a disclaimer cannot contradict that grant. Existing Apache grants are not revoked by a future policy change. A future noncommercial edition would require an ownership/contributor/dependency review and a deliberate release decision, not merely replacing LICENSE. See the [official Apache text](https://www.apache.org/licenses/LICENSE-2.0).

The software license grants rights to the software, not to third-party videos, trademarks or websites. Users must have the necessary rights and follow applicable access restrictions. A “research only” or “delete within 24 hours” statement is not a general exemption.

## Distribution and trust

- Author/developer: **ALOS**. Official source: [Alos21750/UAV-Downloader](https://github.com/Alos21750/UAV-Downloader); official builds: [GitHub Releases](https://github.com/Alos21750/UAV-Downloader/releases).
- UAV is independent of the supported websites and HGhub. Third-party names identify compatibility, not sponsorship.
- Verify releases using [Windows security guidance](../WINDOWS_SECURITY.md); do not disable antivirus, add broad exclusions, or assume a portable ZIP cannot be flagged.
- Report vulnerabilities privately according to [SECURITY.md](../SECURITY.md). Do not post cookies, API keys, signed playback URLs or full authenticated proxy endpoints in issues.
