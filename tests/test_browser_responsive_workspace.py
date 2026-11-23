"""Real Tk geometry, with no network, persistent settings, or visible windows."""
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from unittest.mock import Mock

import pytest

ctk = pytest.importorskip("customtkinter")
from uav_downloader.apps.browse import ModernApp, T


def _browse_geometry():
    root = ctk.CTk()
    root.withdraw()
    try:
        root.overrideredirect(True)
        root.attributes("-alpha", 0)
        root.geometry("980x680+4000+3000")
        tab = ctk.CTkFrame(root)
        tab.pack(fill="both", expand=True)
        app = Mock(_site_key="Hanime1", _tab_frames={"browse": tab})
        app._sync_hanime_filter_button_visibility.side_effect = (
            lambda: ModernApp._sync_hanime_filter_button_visibility(app))
        ModernApp._build_browse_tab(app)
        root.deiconify()
        root.update_idletasks()
        app._search_var.set("saved query")
        for width, compact in [(980, True), (1440, False), (980, True)]:
            root.geometry(f"{width}x680+4000+3000")
            root.update()
            search, sites = app._search_entry, app._site_menu
            assert app._hanime_filter_btn.winfo_viewable()
            assert search.winfo_viewable()
            assert search.winfo_width() >= 240
            assert (search.winfo_rooty() >= sites.winfo_rooty() + sites.winfo_height()) == compact
            assert app._page_lbl.winfo_viewable() and app._grid_scroll.master.winfo_height() > 200
        app._site_key = "MissAV"
        app._sync_hanime_filter_button_visibility()
        root.update_idletasks()
        assert not app._hanime_filter_btn.winfo_viewable()
        app._site_key = "Hanime1"
        app._sync_hanime_filter_button_visibility()
        root.update_idletasks()
        assert app._hanime_filter_btn.winfo_viewable()
        assert app._search_var.get() == "saved query"
    finally:
        root.destroy()


def _submit_controls():
    root = ctk.CTk()
    root.withdraw()
    try:
        app = Mock(_dest="download", _url_input="", _dl_filter="all",
                   _speed_mbps=2.5, _tab_frames={"download": root})
        app._speed_label.return_value = "2.5 MB/s"
        ModernApp._build_download_tab(app)
        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)
        controls = list(widgets(root))
        entry = next(w for w in controls if isinstance(w, ctk.CTkEntry)
                     and w.cget("textvariable") is app._dl_url_var)
        submit = next(w for w in controls if isinstance(w, ctk.CTkButton)
                      and w.cget("text") == T("download_btn"))
        assert entry.master is submit.master
        assert entry._entry.bind("<Return>")
        submit.invoke()
        app._download_url.assert_called_once_with()
    finally:
        root.destroy()


_SCENARIOS = {"browse_geometry": _browse_geometry, "submit_controls": _submit_controls}


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_offline_browser_workspace(scenario):
    # Tk finalizers must not leak into unrelated multithreaded transport tests.
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))
    result = subprocess.run([sys.executable, __file__, scenario], env=env,
                            capture_output=True, text=True, errors="replace", timeout=30)
    if result.returncode == 77:
        pytest.skip(result.stderr.strip())
    assert result.returncode == 0, result.stderr


if __name__ == "__main__":
    try:
        _SCENARIOS[sys.argv[1]]()
    except tk.TclError as exc:
        if "display" not in str(exc).lower():
            raise
        print(f"Tk display unavailable: {exc}", file=sys.stderr)
        sys.exit(77)
