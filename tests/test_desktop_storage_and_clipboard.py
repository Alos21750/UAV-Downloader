import errno
from types import SimpleNamespace

import pytest

from uav_downloader.apps.browse import DownloadManager, ModernApp
from uav_downloader.apps.downloads import classify_download_problem
from uav_downloader.core.storage import StorageWriteError
from uav_downloader.i18n.locales import STRINGS


@pytest.mark.parametrize("number,expected", [(errno.ENOSPC, "disk_full"), (errno.EACCES, "permission"),
    (errno.ENOENT, "storage_path"), (errno.ENOTDIR, "storage_path"), (errno.EIO, "storage_io")])
def test_disk_diagnostic_wins_over_misleading_path_and_restores_from_csv(tmp_path, number, expected):
    error = StorageWriteError("unsupported-network-403", OSError(number, "offline fixture"))
    for value in (error, str(error)):
        p = classify_download_problem(value, "未完成")
        assert p.code == expected and not p.retryable
    m = DownloadManager()
    item = m.add_item("https://jable.tv/videos/test-123/", "fixture", "未完成", str(tmp_path))
    item.error, item.error_code = str(error), "parse_or_unknown"
    file = str(tmp_path / "jobs.csv")
    m.save_csv(file)
    restored = DownloadManager()
    restored.load_csv(file)
    assert restored.get_items()[0].error_code == expected
    assert len({STRINGS[l]["problem_" + expected] for l in ("en","zh","zh-Hans","ja","th")}) >= 4


def test_clipboard_hint_ignores_unrelated_text_and_preserves_user_input():
    safe = "https://jable.tv/videos/test-123/"
    value = [""]
    url_input = SimpleNamespace(get=lambda: value[0], set=lambda s: value.__setitem__(0,s))
    app = SimpleNamespace(_dl_url_var=url_input, _is_closing=False, _rebuilding=False, _clp_text="",
                          _clipboard_poll=lambda: None, after=lambda *_: None)
    app._is_listing_url = lambda url: ModernApp._is_listing_url(app, url)
    for raw in ("https://example.org/private", safe+"?token=private", safe+"#note",
                "private note " + safe, "https://jable.tv.evil.invalid/foo"):
        app.clipboard_get = lambda: raw
        ModernApp._clipboard_poll(app)
        assert value[0] == ""
    for manual in ("user is typing", ""):
        value[0] = manual
        app._clp_text = ""
        app.clipboard_get = lambda: safe
        ModernApp._clipboard_poll(app)
        assert value[0] == (manual or safe)
    next_url = "https://hanime1.me/watch?v=123"
    app.clipboard_get = lambda: next_url
    ModernApp._clipboard_poll(app)
    assert value[0] == next_url
