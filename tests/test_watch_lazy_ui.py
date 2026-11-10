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
        self.visible = True
        self.tabs = {}
        self.selected = ''
        Widget.created.append(self)

    def pack(self, **kwargs): self.visible = True
    def pack_forget(self): self.visible = False
    def configure(self, **kwargs): self.kwargs.update(kwargs)
    def grid(self, **kwargs): self.visible = True
    def grid_remove(self): self.visible = False
    def grid_configure(self, **kwargs): self.kwargs.update(kwargs)
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
        pending={}, next_id=0)

    def after(_delay, callback):
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
