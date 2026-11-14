"""Qt listing model, isolated background fetches and cached thumbnail provider."""
import base64
import json
import time
import threading
from itertools import count

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from uav_downloader.core.browse_service import BROWSERS, fetch_listing, hanime_filter_url, video_badge
from uav_downloader.core.latest_job import LatestJob
from uav_downloader.core.thumbnails import _fetch_thumbnail
from uav_downloader.i18n.locales import T
from uav_downloader.i18n import sites as site_i18n
from uav_downloader.sites.jabletv import JableTVBrowser
from uav_downloader.sites.hanime1 import Hanime1Browser, HANIME1_FILTER_TAG_GROUPS
from uav_downloader import sites
from uav_downloader.apps.downloads import site_name, video_code
from uav_downloader.ui.qt.choice_model import ChoiceModel, ChoiceFilter


def thumbnail_url(site, url):
    if not url:
        return ''
    token = base64.urlsafe_b64encode(json.dumps([site, url]).encode()).decode()
    return 'image://thumbnails/' + token


class VideoModel(QAbstractListModel):
    fields = ('url', 'title', 'duration', 'imageSource', 'selected', 'versionBadge')
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return {Qt.UserRole + i + 1: field.encode() for i, field in enumerate(self.fields)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role):
        i = role - Qt.UserRole - 1
        if not index.isValid() or not 0 <= index.row() < len(self.rows) or not 0 <= i < len(self.fields):
            return None
        return self.rows[index.row()].get(self.fields[i], '')

    def replace(self, videos, site):
        self.beginResetModel()
        self.rows = []
        for video in videos:
            row = dict(video, selected=False, imageSource=thumbnail_url(site, video.get('thumbnail', '')),
                       versionBadge=video_badge(video.get('url'), video.get('title')))
            for field in ('url', 'title', 'duration'):
                row[field] = str(row.get(field) or '')
            self.rows.append(row)
        self.endResetModel()

    def select(self, row, selected):
        if 0 <= row < len(self.rows) and self.rows[row]['selected'] != selected:
            self.rows[row]['selected'] = selected
            self.dataChanged.emit(self.index(row), self.index(row), [Qt.UserRole + 5])

    def select_all(self, value):
        if not self.rows:
            return
        for row in self.rows:
            row['selected'] = value
        self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), [Qt.UserRole + 5])


class ThumbnailProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image,
                         QQuickImageProvider.ForceAsynchronousImageLoading)

    def requestImage(self, identifier, size, requestedSize):
        try:
            site, url = json.loads(base64.urlsafe_b64decode(identifier).decode())
            image = _fetch_thumbnail(url, site)
            if image is not None:
                width, height = image.size
                result = QImage(image.tobytes(), width, height, width * 3, QImage.Format_RGB888).copy()
                size.setWidth(width)
                size.setHeight(height)
                return result
        except Exception:
            pass
        result = QImage(300, 169, QImage.Format_RGB32)
        result.fill(Qt.darkGray)
        size.setWidth(300)
        size.setHeight(169)
        return result


class BrowseController(QObject):
    changed = Signal()
    catalogChanging = Signal()
    catalogChanged = Signal()
    errorOccurred = Signal(str)
    statusChanged = Signal()
    selectionChanged = Signal()

    def __init__(self, tasks, parent=None, fetcher=fetch_listing, catalog_fetcher=Hanime1Browser.fetch_filter_catalog):
        super().__init__(parent)
        self.tasks = tasks
        self.model = VideoModel(self)
        self._worker = LatestJob()
        self._fetcher = fetcher
        self._future = None
        self._load_stop = threading.Event()
        self._closed = False
        self._selection = {}
        self._scan_future = None
        self._scan_stop = threading.Event()
        self._scan_worker = LatestJob()
        self._selection_message = ''
        if hasattr(tasks, 'batchAdded'):
            tasks.batchAdded.connect(self._consume_selection)
        self._site, self._base, self._query, self._page = 'JableTV', '', '', 1
        self._requested_page = 1
        self._has_next = True
        self._categories = []
        self._loading = False
        self._status = 'idle'
        self._load_error = ''
        self._load_started = 0
        self._elapsed_seconds = 0
        self._catalog = Hanime1Browser.filter_catalog()
        self._catalog_fetcher = catalog_fetcher
        self._catalog_worker = LatestJob()
        self._catalog_future = None
        self._filters = dict(query='', genre='', sort='', date='', duration='', tags=[])
        self._jable_model, self._hanime_model = ChoiceModel(self), ChoiceModel(self)
        self._jable_tags, self._hanime_tags = ChoiceFilter(self._jable_model, self), ChoiceFilter(self._hanime_model, self)
        self._jable_model.replace([dict(value=slug, label=name, group=group, url=JableTVBrowser.tag_url(slug))
            for group, tags in JableTVBrowser.SIDEBAR_TAGS.items() for name, slug in tags])
        self._set_hanime_catalog(self._catalog)
        self.retranslate()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def _fetch(self, site, base, query, page, language, cancel_check):
        # Keep injected adapters compatible with the existing five-argument API.
        if self._fetcher is fetch_listing:
            return self._fetcher(site, base, query, page, language, cancel_check=cancel_check)
        return self._fetcher(site, base, query, page, language)

    @Property(bool, notify=changed)
    def loading(self): return self._loading

    @Property('QVariantList', notify=selectionChanged)
    def selection(self): return list(self._selection.values())

    @Slot(result='QVariantList')
    def reviewRows(self):
        from itertools import islice
        result = []
        for url, video in islice(self._selection.items(), 200):
            title = video.get('title') or url.rstrip('/').rsplit('/', 1)[-1]
            result.append(dict(url=url, title=title,
                               details=f'{site_name(url)} · {video_code(url, title)} · {url}'))
        return result

    @Property(int, notify=selectionChanged)
    def selectedCount(self): return len(self._selection)

    @Property(bool, notify=selectionChanged)
    def scanning(self): return self._scan_future is not None

    @Property(str, notify=selectionChanged)
    def selectionMessage(self): return self._selection_message

    def _restore_selection(self):
        for index, video in enumerate(self.model.rows):
            self.model.select(index, video['url'] in self._selection)

    def _consume_selection(self, urls):
        for url in urls:
            self._selection.pop(url, None)
        self._restore_selection()
        self.selectionChanged.emit()

    @Slot()
    def clearSelection(self):
        self._selection.clear()
        self._restore_selection()
        self.selectionChanged.emit()

    @Slot(str)
    def removeSelection(self, url):
        self._selection.pop(url, None)
        self._restore_selection()
        self.selectionChanged.emit()

    @Slot(str)
    def preview(self, url):
        from uav_downloader.ui.preview import open_video_preview
        try:
            opened = open_video_preview(url)
        except (ValueError, OSError):
            opened = False
        if not opened:
            self.errorOccurred.emit(T('preview_failed'))

    @Slot(str, str, result=str)
    def thumbnailSource(self, site, url):
        return thumbnail_url(site, url)

    @Slot()
    def cancelSelectionScan(self): self._scan_stop.set()

    @Slot()
    def selectAllResults(self):
        if self._closed or self._loading or self.scanning:
            return
        self._scan_stop = threading.Event()
        stop = self._scan_stop
        site, base, query = self._site, self._base, self._query
        language = T('missav_lang') if site == 'MissAV' else T('supjav_lang') if site == 'SupJav' else ''
        def scan():
            videos, seen_pages, reason = {}, set(), ''
            for page in count(1):
                if stop.is_set():
                    reason = T('selection_scan_cancelled')
                    break
                try:
                    rows = self._fetch(site, base, query, page, language, stop.is_set)['videos']
                except Exception as error:
                    reason = T('selection_scan_cancelled') if stop.is_set() else str(error)
                    break
                if stop.is_set():
                    reason = T('selection_scan_cancelled')
                    break
                if not rows:
                    break
                signature = frozenset(row.get('url', '') for row in rows)
                if signature in seen_pages:
                    reason = T('selection_scan_repeated')
                    break
                seen_pages.add(signature)
                before = len(videos)
                for row in rows:
                    url = row.get('url', '')
                    if url and sites.VaildateUrl(url):
                        videos.setdefault(url, row)
                if len(videos) == before:
                    reason = T('selection_scan_repeated')
                    break
                if stop.wait(.3):
                    reason = T('selection_scan_cancelled')
                    break
            return list(videos.values()), reason
        self._scan_future = self._scan_worker.submit(scan)
        self.selectionChanged.emit()

    @Slot(str, bool, 'QVariantMap')
    def submitSelection(self, dest, start, options):
        if not self._closed and not self.scanning and self._selection:
            self.tasks.addBatch(list(self._selection.values()), dest, start, options)

    @Slot(str, bool, 'QVariantMap', 'QStringList')
    def submitReviewedSelection(self, dest, start, options, excluded):
        if self._closed or self.scanning or self.tasks.submittingBatch:
            return
        excluded = set(excluded)
        selected = [video for url, video in self._selection.items() if url not in excluded]
        if selected:
            self.tasks.addBatch(selected, dest, start, options)
        if excluded:
            for url in excluded:
                self._selection.pop(url, None)
            self._restore_selection()
            self.selectionChanged.emit()

    @Property(str, notify=changed)
    def query(self): return self._filters['query'] if self._site == 'Hanime1' else self._query

    @Property(int, notify=changed)
    def categoryIndex(self):
        if self.query:
            return -1
        return next((i for i, row in enumerate(self._categories) if row.get('url') == self._base), -1)

    @Property(str, notify=statusChanged)
    def status(self): return self._status

    @Property(str, notify=statusChanged)
    def loadError(self): return self._load_error

    @Property(int, notify=statusChanged)
    def elapsedSeconds(self): return self._elapsed_seconds

    @Property(int, notify=changed)
    def requestedPage(self): return self._requested_page

    @Property('QVariantList', notify=changed)
    def categories(self): return self._categories

    @Property(int, notify=changed)
    def page(self): return self._page

    @Property(bool, notify=changed)
    def hasNext(self): return self._has_next

    @Property(str, notify=changed)
    def site(self): return self._site

    @Property('QStringList', constant=True)
    def siteNames(self): return list(BROWSERS)

    @Property('QVariantMap', notify=changed)
    def filters(self): return dict(self._filters)

    @Property(bool, notify=changed)
    def filtersActive(self): return any(self._filters.values())

    @Property(bool, notify=changed)
    def catalogLoading(self): return self._catalog_future is not None

    @Property(QObject, constant=True)
    def jableTags(self): return self._jable_tags

    @Property('QVariantList', notify=catalogChanged)
    def sidebarTags(self):
        groups = {}
        for row in self._jable_model.rows:
            groups.setdefault(row['group'], []).append(
                dict(label=row['label'], url=row['url']))
        return [dict(key=key, tags=tags) for key, tags in groups.items()]

    @Slot(str)
    def openSidebarTag(self, url):
        if any(row['url'] == url for row in self._jable_model.rows):
            self.load('JableTV', url, '', 1)

    @Property(QObject, constant=True)
    def hanimeTags(self): return self._hanime_tags

    @Property(int, notify=changed)
    def selectedTags(self): return len(self._hanime_model.selection())

    @Property('QVariantMap', notify=catalogChanged)
    def filterCatalog(self):
        keys = dict(genres='genre', sorts='sort', dates='date', durations='duration')
        return {group: [dict(value='', label=T('hanime_filter_any'))] + [
            dict(value=value, label=site_i18n.loc(site_i18n.CATEGORY_I18N,
                Hanime1Browser.filter_url(**{keys[group]: value}), value)) for value in self._catalog[group]]
            for group in keys}

    def _set_hanime_catalog(self, catalog):
        self.catalogChanging.emit()
        # Keep selected values when a live vocabulary temporarily omits them.
        self._catalog = {key: tuple(dict.fromkeys([*catalog[key], *self._catalog[key]])) for key in self._catalog}
        groups = {value: group for group, tags in HANIME1_FILTER_TAG_GROUPS for value in tags}
        self._hanime_model.replace([dict(value=value, label=value, group=groups.get(value, 'other'))
                                   for value in self._catalog['tags']])
        self.retranslate(notify_catalog=False)
        self.catalogChanged.emit()

    @Slot()
    def retranslate(self, notify_catalog=True):
        if notify_catalog:
            self.catalogChanging.emit()
        for row in self._jable_model.rows:
            row['label'] = site_i18n.loc(site_i18n.TAGS, row['value'], row['label'])
        # Group values remain stable; display labels are changed separately.
        if self._jable_model.rows:
            self._jable_model.dataChanged.emit(self._jable_model.index(0), self._jable_model.index(len(self._jable_model.rows) - 1), [Qt.UserRole + 2])
        self.changed.emit()
        if notify_catalog:
            self.catalogChanged.emit()

    @Slot(str)
    def filterJableTags(self, text): self._jable_tags.search(text)

    @Slot(str)
    def filterHanimeTags(self, text): self._hanime_tags.search(text)

    @Slot(int, bool)
    def selectTag(self, row, value):
        self._hanime_model.select(self._hanime_tags.mapToSource(self._hanime_tags.index(row, 0)).row(), value)
        self.changed.emit()

    @Slot()
    def resetDraftTags(self):
        self._hanime_model.restore([])
        self.changed.emit()

    @Slot()
    def openFilters(self):
        self._hanime_model.restore(self._filters['tags'])
        self._hanime_tags.search('')
        if not self._closed and self._catalog_future is None:
            self._catalog_future = self._catalog_worker.submit(self._catalog_fetcher)
        self.changed.emit()

    @Slot()
    def cancelFilters(self):
        self._hanime_model.restore(self._filters['tags'])
        self.changed.emit()

    @Slot('QVariantMap')
    def applyFilters(self, values):
        if self._closed:
            return
        values = dict(dict(query='', genre='', sort='', date='', duration='', tags=[]), **values)
        values['tags'] = self._hanime_model.selection()
        try:
            base = hanime_filter_url(values, self._catalog)
        except Exception as error:
            self.errorOccurred.emit(str(error))
            return
        self._filters = values
        self.load('Hanime1', base, '', 1)

    @Slot(str)
    def search(self, query):
        if self._site == 'Hanime1':
            self._filters['query'] = query.strip()
            self.load(self._site, hanime_filter_url(self._filters, self._catalog), '', 1)
        else:
            self.load(self._site, '', query, 1)

    @Slot(str)
    def loadSite(self, site):
        self._filters = dict(query='', genre='', sort='', date='', duration='', tags=[])
        self.load(site, '', '', 1)

    @Slot(int)
    def chooseCategory(self, row):
        if 0 <= row < len(self._categories):
            self._filters = dict(query='', genre='', sort='', date='', duration='', tags=[])
            self.load(self._site, self._categories[row]['url'], '', 1)

    @Slot(int)
    def chooseJableTag(self, row):
        source = self._jable_tags.mapToSource(self._jable_tags.index(row, 0)).row()
        if 0 <= source < len(self._jable_model.rows):
            self.load('JableTV', self._jable_model.rows[source]['url'], '', 1)

    @Slot(str, str, str, int)
    def load(self, site, base, query, page):
        if self._closed or site not in BROWSERS:
            return
        if site != self._site:
            self._categories = []
            if site != 'Hanime1':
                self._filters = dict(query='', genre='', sort='', date='', duration='', tags=[])
        changed_context = site != self._site or base != self._base or query.strip() != self._query
        self._site, self._base, self._query = site, base, query.strip()
        self._requested_page = max(1, page)
        language = T('missav_lang') if site == 'MissAV' else T('supjav_lang') if site == 'SupJav' else ''
        self._loading = True
        self._status, self._load_error = 'loading', ''
        self._load_started = time.monotonic()
        self._elapsed_seconds = 0
        # Clear obsolete cards immediately; they must not enqueue under a new site.
        if changed_context or (page == 1 and not self.model.rows):
            self.model.replace([], site)
            self._has_next = True
            self._page = 1
        self._load_stop.set()
        self._load_stop = stop = threading.Event()
        self._future = self._worker.submit(lambda: self._fetch(
            site, base, query.strip(), max(1, page), language, stop.is_set))
        self.changed.emit()
        self.statusChanged.emit()

    @Slot()
    def refresh(self):
        if not self._loading:
            self.load(self._site, self._base, self._query, self._page)

    @Slot()
    def retryLoad(self):
        if not self._loading:
            self.load(self._site, self._base, self._query, self._requested_page)

    @Slot(int)
    def turnPage(self, delta):
        self.jumpPage(self._page + delta)

    @Slot(int)
    def jumpPage(self, page):
        if not self._loading and page >= 1 and not (page > self._page and not self._has_next):
            self.load(self._site, self._base, self._query, page)

    @Slot(int, bool)
    def select(self, row, selected):
        self.model.select(row, selected)
        if 0 <= row < len(self.model.rows):
            video = self.model.rows[row]
            if selected:
                self._selection[video['url']] = dict(video)
            else:
                self._selection.pop(video['url'], None)
            self.selectionChanged.emit()

    @Slot(bool)
    def selectAll(self, value):
        for row in range(len(self.model.rows)):
            self.select(row, value)

    @Slot(str)
    def downloadSelected(self, dest):
        if self._closed or self._loading:
            return
        for video in self.model.rows:
            if video['selected']:
                self.tasks.enqueueVideo(video, dest)
        self.model.select_all(False)

    @Slot(str)
    def queueSelected(self, dest):
        if self._closed or self._loading:
            return
        for video in self.model.rows:
            if video['selected']:
                self.tasks.queueVideo(video, dest)
        self.model.select_all(False)

    @Slot()
    def poll(self):
        if self._closed:
            return
        if self._scan_future is not None and self._scan_future.done():
            future, self._scan_future = self._scan_future, None
            try:
                videos, reason = future.result()
                self._selection.update((video['url'], video) for video in videos)
                self._selection_message = T('selection_all_partial', count=len(videos), reason=reason) if reason else T('selection_all_done', count=len(videos))
                self._restore_selection()
            except Exception as error:
                self.errorOccurred.emit(str(error))
            self.selectionChanged.emit()
        if self._loading:
            elapsed = int(time.monotonic() - self._load_started)
            if elapsed != self._elapsed_seconds:
                self._elapsed_seconds = elapsed
                self.statusChanged.emit()
        if self._catalog_future is not None and self._catalog_future.done():
            future, self._catalog_future = self._catalog_future, None
            try:
                self._set_hanime_catalog(future.result())
            except Exception as error:
                self.errorOccurred.emit(str(error))
            self.changed.emit()
        if self._future is None or not self._future.done():
            return
        future, self._future = self._future, None
        self._loading = False
        try:
            data = future.result()
            self._base = data['base']
            self._categories = data['categories']
            self._has_next = bool(data['videos'])
            self._status = 'success' if data['videos'] else 'empty' if self._requested_page == 1 else 'end'
            if data['videos'] or self._requested_page == 1:
                self.model.replace(data['videos'], self._site)
                self._restore_selection()
                self._page = self._requested_page
        except Exception as error:
            self._status, self._load_error = 'error', str(error)
            self.errorOccurred.emit(str(error))
        self.changed.emit()
        self.statusChanged.emit()

    @Slot()
    def close(self):
        self._closed = True
        self._load_stop.set()
        self._scan_stop.set()
        self._scan_worker.close()
        self._future = None
        self.timer.stop()
        self._worker.close()
        self._catalog_future = None
        self._catalog_worker.close()
