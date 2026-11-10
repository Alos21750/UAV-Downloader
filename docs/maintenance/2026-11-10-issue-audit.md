# Issue and pull-request audit — 2026-11-10

Repository: [Alos21750/UAV-Downloader](https://github.com/Alos21750/UAV-Downloader).
Starting source: `9c09ab5` / v3.4.0. This is a maintenance snapshot, **not a
statement that a new release has been published or verified**.

## Coverage

Reviewed the bodies and available comments of all **56 issues** returned by
GitHub, including closed issues, and all **4 pull requests**. The issue inventory
is #1–21, #23–48, #52–55, and #57–61. No #22 issue was returned; its contents or
history were not inferred.

- **4 open issues:** #54, #55, #57, #59; **52 closed issues**.
- **0 open PRs:** #49 (SupJav rate-limit waits), #50 (local-file subtitles),
  #51 (slow-download requeue), and #56 (Thai localization) are all merged.
- The only new issue discussion after v3.4.0 is on #59. The latest #58 source
  selection and #61 Huangguo threads contain no new regression report.

## Open-issue disposition

| Issue | Evidence and action | Disposition |
| --- | --- | --- |
| [#54](https://github.com/Alos21750/UAV-Downloader/issues/54) — storage failure | The report includes an AI-generated hardware diagnosis, not proof of a UAV-caused controller failure. Earlier releases added storage fail-stop, atomic writes, and operation/path diagnostics. Request the current app version, redacted UAV error, disk type/free space, and corresponding Windows storage-event IDs. Do not stress-test a suspect drive or assert a firmware cause. | Keep open; no reporter retest since the existing diagnostic request. |
| [#55](https://github.com/Alos21750/UAV-Downloader/issues/55) — embedded preview | The delivered Preview action opens the system browser. It does not implement the requested in-app streaming player. Embedded playback needs a separately scoped design and security review. | Keep open as an unimplemented enhancement, not a completed bug fix. |
| [#57](https://github.com/Alos21750/UAV-Downloader/issues/57) — download failure | The cropped ENOENT/storage error cannot establish the failing operation or path. Earlier operation/path reporting needs a current-version retest and a redacted complete error. Browser access does not prove the media source or destination is usable. | Keep open pending evidence; do not merge unrelated CF and storage diagnoses. |
| [#59](https://github.com/Alos21750/UAV-Downloader/issues/59) — SupJav/proxy | The Nov. 8 Taipei-time screenshots show v3.4.0 detecting a Windows manual proxy and displaying a CF-block message. They do not prove the proxy was ignored. Reproduced misleading network/HTTP classification and stale Browser error UI; local fixes and regression tests are described below. | Keep open for a released-build retest. Genuine site challenges remain possible. |

## Local regression work under validation

- **#59 diagnostics:** exhausted connection/proxy retries, ordinary HTTP errors,
  and rejected off-allowlist redirects no longer masquerade as Cloudflare
  challenges. Challenge markers remain recognized. Successful fallback mirrors
  still win; retry and rate-limit wait budgets are preserved. Diagnostics never
  interpolate proxy credentials, raw exception text, cookies, or response bodies.
- **#59 Browser state:** a generic failure replaces a stale CF placeholder;
  failure to load a subsequent page preserves already loaded results.
- **Proxy evidence:** actual `curl_cffi` HTTPS CONNECT requests reach a loopback
  test proxy in both manual and Windows-system modes. The proxy rejects the
  tunnel without connecting externally. This establishes routing in the tested
  path, not universal proxy compatibility or a CF bypass.
- Related local work includes incremental Watcher category widgets, cancellable
  and bounded Huangguo metadata work, and correct query-only HLS URI resolution
  against the final response URL. These are not grounds to close unrelated
  reporter issues without the applicable release and verification gates.

The initial #59 regression fixture produced **13 failures / 6 passes** before
the fix. After fixes and two additional mixed/recovered-failure cases, the focused
mirror, CF, proxy, and SupJav suite passed **81 tests**. Independent review found
no blocking issue in these changes. Frozen executable/UI checks and public asset
verification remain separate release requirements.

## Closed-thread regression themes

Retain coverage for URL gates, locale paths and pagination (#1/#7/#11/#12/#15/#26);
false-empty versus challenge/network failures (#14/#16/#17/#20/#48); startup and
queue responsiveness (#17/#19/#24/#25); shared TLS sessions (#24/#27); destination
and temporary-file I/O (#44); resume tails and source selection (#29/#31/#43/#52/#58);
and subtitle/download queue cancellation and requeue (#33/#41/#46, PRs #50/#51).
No evidence in this audit alone warrants reopening those closed threads.

## Historical security guidance

Recommend a corrective comment on [#18](https://github.com/Alos21750/UAV-Downloader/issues/18):
older advice to disable antivirus/add exclusions, or assurances that portable
builds avoid detection, must not guide current users. Link
[Windows download verification](../../WINDOWS_SECURITY.md), retain protection,
and collect the threat name, detection time, relevant versions, and SHA-256.
Matching hashes establish identical bytes; attestations establish build
provenance. Neither proves safety. Do not restore quarantined files merely to
upload them. This correction does not require reopening the old issue.

No issue replies, closures, merges, or releases were performed by this audit.
