from types import SimpleNamespace

from uav_downloader.apps.browse import DownloadManager, ModernApp


class _Destination:
    def get(self):
        return "downloads"


class _DownloadManager:
    def __init__(self):
        self.added = []
        self.enqueued = []

    def add_item(self, url, **kwargs):
        self.added.append((url, kwargs))

    def enqueue(self, url, dest):
        self.enqueued.append((url, dest))

    def contains(self, url):
        return any(existing == url for existing, _kwargs in self.added)


def _missav_selection():
    manager = _DownloadManager()
    app = SimpleNamespace(
        _site_key="MissAV",
        _current_base_url="https://missav.ai/en/search/Yue%20Kelan",
        _videos=[],
        _selected_urls=set(),
        _selected_videos={},
        _selected_source_subtitle_evidence={},
        _dest_var=_Destination(),
        _dlmgr=manager,
        _set_card_selected=lambda *_args: None,
        _update_selection_count=lambda: None,
    )
    app._source_subtitle_evidence_for_video = lambda video: (
        ModernApp._source_subtitle_evidence_for_video(app, video)
    )
    app._clear_selection_in_place = lambda: (
        ModernApp._clear_selection_in_place(app)
    )
    return app, manager


def _select_search_pages(app):
    first_page = [
        {
            "url": f"https://missav.ai/en/mcy{number:04d}",
            "title": f"MCY{number:04d} Yue Kelan title {number}",
        }
        for number in range(1, 13)
    ]
    second_page = [
        {
            "url": "https://missav.ai/dm26/en/cus-1224",
            "title": "CUS-1224 Yue Kelan title",
        },
        {
            "url": "https://missav.ai/dm26/en/mdx0241-04",
            "title": "MDX0241-04 Yue Kelan title",
        },
    ]

    app._videos = first_page
    ModernApp._select_all_on_page(app)
    app._videos = second_page
    ModernApp._select_all_on_page(app)
    return first_page + second_page


def test_missav_select_all_adds_every_compact_code_with_displayed_title():
    app, manager = _missav_selection()
    selected = _select_search_pages(app)

    assert list(app._selected_videos) == [video["url"] for video in selected]

    ModernApp._add_selected_to_queue(app)

    assert [url for url, _kwargs in manager.added] == [
        video["url"] for video in selected
    ]
    assert [kwargs["name"] for _url, kwargs in manager.added] == [
        video["title"] for video in selected
    ]
    assert manager.enqueued == []
    assert not app._selected_urls
    assert not app._selected_videos


def test_missav_select_all_downloads_every_compact_code_in_grid_order():
    app, manager = _missav_selection()
    selected = _select_search_pages(app)

    ModernApp._download_selected(app)

    assert [url for url, _kwargs in manager.added] == [
        video["url"] for video in selected
    ]
    assert manager.enqueued == [
        (video["url"], "downloads") for video in selected
    ]


def test_readding_a_persisted_queue_item_refreshes_its_displayed_title():
    manager = DownloadManager()
    url = "https://missav.ai/en/mdhg0020"
    original = manager.add_item(url, name="MDHG0020")

    refreshed = manager.add_item(
        url, name="MDHG0020 Yue Kelan interview title")

    assert refreshed is original
    assert refreshed.name == "MDHG0020 Yue Kelan interview title"


def test_selection_summary_distinguishes_duplicate_and_unsupported_urls():
    app, manager = _missav_selection()
    duplicate = 'https://missav.ai/en/mdhg0020'
    unsupported = 'https://example.test/nope'
    manager.add_item(duplicate, name='Already queued')
    app._selected_urls.update((duplicate, unsupported))
    app._selected_videos[duplicate] = {
        'url': duplicate, 'title': 'Fresh displayed title'}
    app._selected_videos[unsupported] = {
        'url': unsupported, 'title': 'Unsupported'}

    summary = ModernApp._add_selected_to_queue(app)

    assert summary.requested == 2
    assert summary.added == 0
    assert summary.duplicates == 1
    assert summary.unsupported == 1
