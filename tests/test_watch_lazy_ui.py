"""Headless contract tests for lazy Watcher category widgets and stable state."""
from types import MethodType, SimpleNamespace

import pytest

from uav_downloader.apps import watch
from uav_downloader.apps.watch_categories import SITES, iter_targets, selection_key


class Var:
    def __init__(self, master=None, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    created = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.visible = False
        self.tabs = {}
        self.selected = ''
        self.grid_calls = 0
        self.grid_remove_calls = 0
        self.pack_calls = 0
        self.pack_forget_calls = 0
        self.last_pack = {}
        Widget.created.append(self)

    def pack(self, **kwargs):
        self.visible = True
        self.pack_calls += 1
        self.last_pack = kwargs
    def pack_forget(self):
        self.visible = False
        self.pack_forget_calls += 1
    def configure(self, **kwargs): self.kwargs.update(kwargs)
    def grid(self, **kwargs):
        self.visible = True
        self.grid_calls += 1
    def grid_remove(self):
        self.visible = False
        self.grid_remove_calls += 1
    def grid_configure(self, **kwargs):
        self.kwargs.update(kwargs)
        # Tk grid configure remaps a previously grid-removed child.
        self.visible = True
    def grid_columnconfigure(self, *args, **kwargs): pass
    def add(self, name):
        self.tabs[name] = Widget()
        self.selected = self.selected or name
    def tab(self, name): return self.tabs[name]
    def get(self): return self.selected
    def set(self, name): self.selected = name


@pytest.fixture
def lazy_app(monkeypatch):
    Widget.created = []
    for name in ('CTkFrame', 'CTkScrollableFrame', 'CTkLabel', 'CTkCheckBox', 'CTkTabview'):
        monkeypatch.setattr(watch.ctk, name, Widget)
    monkeypatch.setattr(watch.tk, 'BooleanVar', Var)
    app = SimpleNamespace(
        _check_vars={}, _category_groups=[], _filter_groups=[], _target_widgets=[],
        _category_columns=3, _build_gen=0, _is_closing=False,
        _category_filter_var=Var(value=''), _selected_count_lbl=Widget(),
        pending={}, next_id=0, scheduled_delays=[])

    def after(_delay, callback):
        app.scheduled_delays.append(_delay)
        app.next_id += 1
        app.pending[app.next_id] = callback
        return app.next_id

    app.after = after
    app.after_cancel = lambda handle: app.pending.pop(handle, None)
    for name in (
        '_build_category_tabs', '_iter_category_widgets', '_on_category_tab_changed',
        '_active_category_site', '_run_category_build_batch', '_cancel_category_work',
        '_schedule_category_filter', '_filter_targets', '_update_selected_count',
        '_toggle_select_group', '_sync_group_select', '_sync_select_all_vars',
        '_get_selected_targets', '_restore_target_checks',
        '_set_category_group_visible', '_reflow_category_grid',
    ):
        setattr(app, name, MethodType(getattr(watch.SmallToolApp, name), app))
    app._build_category_tabs(Widget(), 'Arial')
    return app


def step(app):
    handle = next(iter(app.pending))
    callback = app.pending.pop(handle)
    callback()


def drain(app):
    for _ in range(500):
        if not app.pending:
            return
        step(app)
    pytest.fail('Category rendering did not terminate')


def test_category_state_exists_before_any_widgets_and_only_active_site_renders(lazy_app):
    app = lazy_app
    assert len(app._check_vars) == 545
    assert app._target_widgets == []
    step(app)
    assert 0 < len(app._target_widgets) < 129
    drain(app)
    assert len(app._target_widgets) == 129
    assert app._category_site_views['JableTV']['complete'] is True
    assert set(app._category_site_views) == {'JableTV'}


def test_saved_hidden_selections_and_unbuilt_group_select_all_survive(lazy_app):
    app = lazy_app
    target = next(iter(iter_targets('Huangguo')))
    app._restore_target_checks([{'site': 'Huangguo', 'id': target['id']}])
    assert app._get_selected_targets()[0]['site'] == 'Huangguo'
    group = SITES['Hanime1']['groups'][1]
    app._check_vars[f'Hanime1|__group__|{group["id"]}'].set(True)
    app._toggle_select_group('Hanime1', group['id'])
    assert all(app._check_vars[selection_key('Hanime1', target['id'])].get()
               for target in group['targets'])
    assert set(app._category_site_views) == {'JableTV'}
    assert app._selected_count_lbl.kwargs['text'].startswith(str(len(group['targets']) + 1))


def test_tab_change_pauses_old_batches_and_resumes_without_duplicate_widgets(lazy_app):
    app = lazy_app
    step(app)
    partial = len(app._target_widgets)
    app._category_tabview.set(app._category_tab_names['Huangguo'])
    app._on_category_tab_changed()
    drain(app)
    assert len(app._target_widgets) == partial + 2
    app._category_tabview.set(app._category_tab_names['JableTV'])
    app._on_category_tab_changed()
    drain(app)
    assert len(app._target_widgets) == 131
    app._on_category_tab_changed()
    assert not app.pending


@pytest.mark.parametrize('state', ['generation', 'closed'])
def test_obsolete_batches_do_not_touch_widgets(lazy_app, state):
    app = lazy_app
    old_callbacks = list(app.pending.values())
    if state == 'generation':
        app._build_gen += 1
    else:
        app._is_closing = True
    for callback in old_callbacks:
        callback()
    assert app._target_widgets == []


def test_filter_is_debounced_and_does_not_materialize_hidden_tabs(lazy_app):
    app = lazy_app
    drain(app)
    app._category_filter_var.set('no-such-category-fixture')
    app._schedule_category_filter()
    app._schedule_category_filter()
    assert len(app.pending) == 1
    drain(app)
    assert all(not widget.visible for widget, _label in app._target_widgets)
    assert set(app._category_site_views) == {'JableTV'}
    app._category_tabview.set(app._category_tab_names['Huangguo'])
    app._on_category_tab_changed()
    drain(app)
    assert all(not widget.visible for widget, _label in app._target_widgets)


def test_cancel_removes_render_and_filter_work(lazy_app):
    app = lazy_app
    app._schedule_category_filter()
    assert len(app.pending) == 2
    app._cancel_category_work()
    assert not app.pending


def test_cancelled_batch_stays_stale_when_same_tab_is_selected_again(lazy_app):
    app = lazy_app
    stale = next(iter(app.pending.values()))
    app._on_category_tab_changed()
    stale()
    assert app._target_widgets == []
    drain(app)
    assert len(app._target_widgets) == 129


def test_rebuild_restores_unvisited_selections_without_rendering_all_tabs(lazy_app):
    app = lazy_app
    target = next(iter(iter_targets('Hanime1')))
    app._restore_target_checks([{'site': 'Hanime1', 'id': target['id']}])
    saved_targets = app._get_selected_targets()
    stale = next(iter(app.pending.values()))
    app._build_gen += 1
    app._cancel_category_work()
    app._check_vars = {}
    app._category_groups = []
    app._filter_groups = []
    app._target_widgets = []
    app._build_category_tabs(Widget(), 'Arial')
    app._restore_target_checks(saved_targets)
    app._sync_select_all_vars()
    app._category_tabview.set(app._category_tab_names['Huangguo'])
    app._category_filter_var.set('AI')
    app._on_category_tab_changed()
    stale()
    assert app._target_widgets == []
    drain(app)
    assert len(app._target_widgets) == 2
    assert app._get_selected_targets() == saved_targets
    assert app._active_category_site() == 'Huangguo'


def test_late_materialization_observes_already_selected_target_variables(lazy_app):
    app = lazy_app
    target = next(iter(iter_targets('Huangguo')))
    key = selection_key('Huangguo', target['id'])
    app._restore_target_checks([{'site': 'Huangguo', 'id': target['id']}])
    app._category_tabview.set(app._category_tab_names['Huangguo'])
    app._on_category_tab_changed()
    drain(app)
    selected_checkbox = next(widget for widget, _label in app._target_widgets
                             if widget.kwargs['variable'] is app._check_vars[key])
    assert selected_checkbox.kwargs['variable'].get() is True


def test_first_visit_geometry_work_is_linear_without_blank_repacking(lazy_app):
    app = lazy_app
    app._category_tabview.set(app._category_tab_names['Hanime1'])
    app._on_category_tab_changed()
    drain(app)
    widgets = [widget for widget, _label in app._target_widgets]
    assert len(widgets) == 272
    assert sum(widget.grid_calls for widget in widgets) == 272
    assert sum(widget.grid_remove_calls for widget in widgets) == 0
    groups = [group['frame'] for group in app._filter_groups]
    assert len(groups) == 13
    assert sum(frame.pack_calls for frame in groups) == 13
    assert sum(frame.pack_forget_calls for frame in groups) == 0
    assert all(delay >= 16 for delay in app.scheduled_delays)


def test_unchanged_filter_never_regrids_or_repacks_existing_content(lazy_app):
    app = lazy_app
    drain(app)
    before = [(w.grid_calls, w.grid_remove_calls, w.pack_calls, w.pack_forget_calls)
              for w in Widget.created]
    app._filter_targets()
    app._filter_targets()
    after = [(w.grid_calls, w.grid_remove_calls, w.pack_calls, w.pack_forget_calls)
             for w in Widget.created]
    assert after == before


def test_new_widgets_honor_filter_without_touching_prior_batches(lazy_app):
    app = lazy_app
    app._category_filter_var.set('no-such-category-fixture')
    step(app)
    prior_widgets = [widget for widget, _label in app._target_widgets]
    before = [(w.grid_calls, w.grid_remove_calls) for w in prior_widgets]
    step(app)
    assert [(w.grid_calls, w.grid_remove_calls) for w in prior_widgets] == before
    assert all(not widget.visible for widget, _label in app._target_widgets)
    assert all(not group['frame'].visible for group in app._filter_groups)


def test_filter_reinserts_only_changed_group_before_later_visible_group(lazy_app):
    app = lazy_app
    drain(app)
    first, later = app._filter_groups[:2]
    # Give one earlier group a nonmatching label while keeping later groups.
    first['items'] = [(widget, 'first-only') for widget, _label in first['items']]
    for group in app._filter_groups[1:]:
        group['items'] = [(widget, 'later-only') for widget, _label in group['items']]
    app._category_filter_var.set('later-only')
    app._filter_targets()
    assert first['frame'].visible is False
    later_counts = (later['frame'].pack_calls, later['frame'].pack_forget_calls)
    app._category_filter_var.set('')
    app._filter_targets()
    assert first['frame'].last_pack['before'] is later['frame']
    assert (later['frame'].pack_calls, later['frame'].pack_forget_calls) == later_counts


def test_render_batches_never_run_a_full_filter_pass(lazy_app):
    app = lazy_app
    app._filter_targets = lambda: pytest.fail('Full filtering inside render batch')
    drain(app)
    assert len(app._target_widgets) == 129


def test_resize_preserves_hidden_checkboxes_without_repacking_groups(lazy_app):
    app = lazy_app
    drain(app)
    app._category_filter_var.set('no-such-category-fixture')
    app._filter_targets()
    before = [(group['frame'].pack_calls, group['frame'].pack_forget_calls)
              for group in app._filter_groups]
    hidden_before = [widget.grid_remove_calls for widget, _label in app._target_widgets]
    app._category_columns = 2
    app._reflow_category_grid()
    assert all(not widget.visible for widget, _label in app._target_widgets)
    assert [widget.grid_remove_calls for widget, _label in app._target_widgets] == [
        calls + 1 for calls in hidden_before]
    assert [(group['frame'].pack_calls, group['frame'].pack_forget_calls)
            for group in app._filter_groups] == before


def test_filter_change_during_build_applies_once_to_existing_and_new_items(lazy_app):
    app = lazy_app
    app._category_filter_var.set('no-such-category-fixture')
    step(app)
    old_widgets = [widget for widget, _label in app._target_widgets]
    app._category_filter_var.set('')
    app._filter_targets()
    old_counts = [(w.grid_calls, w.grid_remove_calls) for w in old_widgets]
    step(app)
    assert all(widget.visible for widget, _label in app._target_widgets)
    assert [(w.grid_calls, w.grid_remove_calls) for w in old_widgets] == old_counts
