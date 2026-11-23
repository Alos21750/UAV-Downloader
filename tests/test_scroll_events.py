from types import SimpleNamespace

import pytest

from uav_downloader.apps import browse, watch
from uav_downloader.ui import translation_settings


@pytest.mark.parametrize('surface', [browse, watch, translation_settings])
def test_scroll_event_ignores_unresolved_tcl_paths_but_keeps_children(surface):
    frame_type = surface.ScrollableFrame
    frame = object.__new__(frame_type)
    frame._parent_canvas = SimpleNamespace(master=None, name='canvas')
    assert not frame._check_if_valid_scroll('.!unregistered')
    assert not frame._check_if_valid_scroll(SimpleNamespace(master='.!gone'))
    assert frame._check_if_valid_scroll(SimpleNamespace(master=frame._parent_canvas))
    assert not frame._check_if_valid_scroll(SimpleNamespace(master=None))
