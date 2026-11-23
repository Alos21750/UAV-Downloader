"""Real Tk layout probe: isolate prefs/portable state, never start a scan."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows desktop geometry')
@pytest.mark.timeout(75)  # The real UI subprocess below has its own 60-second bound.
def test_watcher_controls_fit_normal_and_narrow_windows(tmp_path):
    env = dict(os.environ, APPDATA=str(tmp_path), LOCALAPPDATA=str(tmp_path),
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    result = subprocess.run([sys.executable, __file__, str(tmp_path)], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())


def _probe(profile):
    from uav_downloader.core import paths
    paths.select_portable_state_dir = lambda _path: profile / 'watcher-state'
    from uav_downloader.core import crashlog
    crashlog.install = lambda: None
    from uav_downloader.apps import watch

    watch.config.set_ui_lang('en')
    watch.SmallToolApp._fit_window_to_work_area = lambda self: None
    unexpected_starts = []
    watch.SmallToolApp._auto_start_worker = lambda self: unexpected_starts.append(True)
    app = watch.SmallToolApp()
    # Mapped for actual Tk metrics but transparent and never requests focus.
    app.attributes('-alpha', 0)
    def settle():
        until = time.monotonic() + .45
        while time.monotonic() < until:
            app.update()
            time.sleep(.01)
    def inside(widget):
        x = widget.winfo_rootx() - app.winfo_rootx()
        y = widget.winfo_rooty() - app.winfo_rooty()
        assert widget.winfo_ismapped(), widget
        assert x >= 0 and y >= 0, (widget, x, y)
        assert x + widget.winfo_width() <= app.winfo_width() + 1, (widget, x)
        assert y + widget.winfo_height() <= app.winfo_height() + 1, (widget, y)
    try:
        # Windows clamps managed windows to the available desktop (the hosted
        # runner is narrower than 1180). Exercise the real initial size on
        # each machine; retain the same explicit small-window requirement.
        normal = watch._initial_window_size(*watch._logical_work_area(app))
        for width, height in (normal, (640, 500)):
            app.geometry(f'{width}x{height}+0+0')
            settle()
            scale = app._get_window_scaling()
            assert abs(app.winfo_width() / scale - width) <= 2, app.winfo_width()
            for widget in (app._start_btn, app._stop_btn, app._check_now_btn,
                           app._schedule_btn, app._activity_toggle_btn, app._status_lbl,
                           app._settings_toggle_btn, app._category_filter_box):
                inside(widget)
            tab = app._category_tabview
            inside(tab)
            assert tab.winfo_height() / scale >= 128
            app._set_activity_visible(True)
            settle()
            with_activity = tab.winfo_height() / scale
            assert tab.winfo_height() / scale >= 111, tab.winfo_height()
            inside(app._activity_panel)
            app._set_activity_visible(False)
            app._set_settings_expanded(True)
            settle()
            inside(app._settings_toggle_btn)
            inside(app._start_btn)
            assert app._settings_scroll._parent_canvas.winfo_height() / scale >= 90
            altered = 575 if height >= 600 else 620
            app.geometry(f'{width}x{altered}+0+0')
            settle()
            assert not app._selection_panel.winfo_ismapped()
            inside(app._settings_toggle_btn)
            inside(app._start_btn)
            app.geometry(f'{width}x{height}+0+0')
            app._set_settings_expanded(False)
            settle()
            if width < 700:
                name = app._category_tab_names['MissAV']
                for button in tab._segmented_button._buttons_dict.values():
                    inside(button)
                app._category_filter_var.set('護士')
                tab.set(name)
                app._on_category_tab_changed()
                until = time.monotonic() + 10
                while not app._category_site_views['MissAV']['complete']:
                    assert time.monotonic() < until
                    settle()
                settle()
                role = next(cb for group in app._filter_groups if group['site'] == 'MissAV'
                            for cb, _ in group['items'] if cb.winfo_ismapped()
                            and cb.cget('text') == 'Nurse')
                inside(role)
                role.select()
                assert any(t['id'] == 'role:nurse' for t in app._get_selected_targets())
            print(f'{width}x{height}: categories {tab.winfo_height()/scale:.0f}px '
                  f'({with_activity:.0f}px with activity); '
                  'all five actions, status and settings inside window')
        assert not unexpected_starts
    finally:
        app.destroy()


if __name__ == '__main__':
    _probe(Path(sys.argv[1]))
