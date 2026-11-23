from types import SimpleNamespace

import pytest

from uav_downloader.sites import missav
from uav_downloader.sites.base import M3U8Crawler


@pytest.mark.parametrize("status", [429, 503])
def test_same_job_honors_retry_after_without_blocking_other_origins(monkeypatch, status):
    now = [100.0]
    monkeypatch.setattr(missav.time, "monotonic", lambda: now[0])
    calls = []
    def fetch(self, url, **kw):
        calls.append(url)
        return SimpleNamespace(status_code=status if len(calls) == 1 else 200,
                               headers={"Retry-After": "15"})
    monkeypatch.setattr(M3U8Crawler, "_media_get", fetch)
    job = missav.SiteMissAV.__new__(missav.SiteMissAV)
    url = "https://cdn.example/a.ts?private=1"
    assert job._media_get(url).status_code == status
    with pytest.raises(Exception, match="Retry-After") as exc:
        job._media_get("https://cdn.example/b.ts")
    assert "private" not in str(exc.value)
    assert job._media_get("https://other.example/a.ts").status_code == 200
    now[0] += 15
    assert job._media_get(url).status_code == 200
    assert calls == [url, "https://other.example/a.ts", url]


@pytest.mark.parametrize("status,header,paused", [
    (503, "Thu, 01 Jan 1970 00:02:00 GMT", True),
    (503, "malformed", False), (503, "-1", False),
    (403, "120", False), (429, "0", False),
])
def test_date_and_invalid_cooldowns(monkeypatch, status, header, paused):
    monkeypatch.setattr(missav.time, "time", lambda: 100)
    monkeypatch.setattr(missav.time, "monotonic", lambda: 200)
    monkeypatch.setattr(M3U8Crawler, "_media_get", lambda *a, **k:
                        SimpleNamespace(status_code=status, headers={"Retry-After": header}))
    job = missav.SiteMissAV.__new__(missav.SiteMissAV)
    job._media_get("https://cdn.example/a")
    if paused:
        with pytest.raises(missav._MediaCooldown):
            job._media_get("https://cdn.example/b")
    else:
        assert job._media_get("https://cdn.example/b").status_code == status
