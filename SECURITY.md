# Security policy

## Reporting a vulnerability

Do not open a public Issue for a vulnerability that could expose user data,
credentials, or arbitrary code execution. Use GitHub's private vulnerability
reporting feature for this repository. Include the affected version, platform,
reproduction steps, impact, and any proposed mitigation.

For ordinary parser failures, site changes, or download errors with no security
impact, use the public Issue tracker instead.

## Secrets and local data

The project does not ship an API key. User-supplied LLM API keys are encrypted
with Windows DPAPI for the current account. Reports and logs must not include
API keys, cookies, proxy credentials, tokens, personal paths, or downloaded
content.

## Release verification

Official Windows assets are published through GitHub Releases with
`UAV_SHA256SUMS.txt` and GitHub artifact attestations. See
[`WINDOWS_SECURITY.md`](./WINDOWS_SECURITY.md) before running a downloaded
binary. A SmartScreen reputation warning is not the same event as a Defender
Antivirus detection; never disable protection or add a broad exclusion merely
to run the application.
