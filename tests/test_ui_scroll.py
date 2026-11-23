from types import SimpleNamespace

from uav_downloader.ui.scroll import ScrollableFrame


def test_scroll_rejects_unresolved_event_widget_and_keeps_child_scrolling():
    frame = object.__new__(ScrollableFrame)
    frame._parent_canvas = SimpleNamespace(master=None)
    assert not frame._check_if_valid_scroll('.!unknown')
    assert not frame._check_if_valid_scroll(SimpleNamespace(master='.!destroyed'))
    assert frame._check_if_valid_scroll(frame._parent_canvas)
    assert frame._check_if_valid_scroll(SimpleNamespace(master=frame._parent_canvas))
