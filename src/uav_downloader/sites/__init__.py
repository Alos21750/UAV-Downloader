"""Site registry for featured and compatibility-only URL adapters."""

from __future__ import annotations

from . import hanime1, huangguo, jabletv, missav, supjav
from .legacy import sites_91porn, sites_javdb


FEATURED_SITE_CLASSES = (
    jabletv.SiteJableTV,
    jabletv.SiteJableTV_Backup,
    missav.SiteMissAV,
    supjav.SiteSupJav,
    hanime1.SiteHanime1,
    huangguo.SiteHuangguo,
)

LEGACY_URL_ONLY_SITE_CLASSES = (
    sites_91porn.SiteJableOrg,
    sites_91porn.SiteThisAV,
    sites_91porn.SitePigAV,
    sites_91porn.SitePorn5F,
    sites_91porn.Site85Tube,
    sites_91porn.Site91Porn,
    sites_91porn.SitePornBest,
    sites_javdb.SiteJavdbLive,
    sites_javdb.SiteHAnimeXYZ,
    sites_javdb.SitePornTW,
    sites_javdb.SitePornJP,
    sites_javdb.SitePornHK,
    sites_javdb.SitePornHoHo,
    sites_javdb.SitePornNVR,
    sites_javdb.SiteVideo01,
    sites_javdb.SitePornLuLu,
    sites_javdb.SiteMIEN321,
    sites_javdb.SiteAApp11,
    sites_javdb.SiteSeselah,
    sites_javdb.SiteXJISHI,
)

# Backward-compatible public names used throughout the original application.
siteList = FEATURED_SITE_CLASSES + LEGACY_URL_ONLY_SITE_CLASSES
siteUrlList = (jabletv.JableTVList,)


def validate_url(url):
    for site in siteList:
        if site.validate_url(url):
            return site
    return None


def VaildateUrl(url):  # noqa: N802 - legacy API typo retained for v2 callers
    return validate_url(url)


def create_site(
        url, savepath="", silence=False, max_workers=None,
        resolution=None, filename_template=None):
    site = validate_url(url)
    if site is None:
        return None
    kwargs = {
        'savepath': savepath,
        'silence': silence,
        'max_workers': max_workers,
    }
    if resolution is not None:
        kwargs['resolution'] = resolution
    if filename_template is not None:
        kwargs['filename_template'] = filename_template
    return site(url, **kwargs)


def CreateSite(  # noqa: N802
        url, savepath="", silence=False, max_workers=None,
        resolution=None, filename_template=None):
    return create_site(
        url, savepath, silence, max_workers,
        resolution, filename_template)


def create_site_url_list(url, silence=False):
    for url_list in siteUrlList:
        candidate = url_list(url, silence=silence)
        if candidate.isVaildLinks():
            return candidate
    return None


def CreateSiteUrlList(url, silence=False):  # noqa: N802
    return create_site_url_list(url, silence)


def consoles_main(url, dest=None, max_workers=None):
    from uav_downloader.core.stop import (
        StopContext, StopReason, DownloadStopped, StopTimeoutError, console_stop_scope,
    )
    from uav_downloader.core.storage import StorageWriteError
    if not url:
        try:
            url = input("輸入支援的網址: ")
        except KeyboardInterrupt:
            return 130
    context = StopContext()
    job = None
    result = 1
    with console_stop_scope(context):
        try:
            job = create_site(url, dest, max_workers=max_workers)
            context.check()
            if job is None:
                print(f"不支援的網址: {url}")
                result = 2
            else:
                valid = job.is_url_vaildate()
                context.check()
                if not valid:
                    print(f"無法解析影片: {url}")
                else:
                    completed = job.start_download()
                    context.check()
                    if completed is False:
                        print("下載未完成")
                    else:
                        print("下載完成!")
                        result = 0
        except (DownloadStopped, KeyboardInterrupt):
            context.request(StopReason.SHUTDOWN)
            print("已請求停止下載，等待工作者退出")
        except Exception as exc:
            if isinstance(exc, StorageWriteError):
                context.request(StopReason.STORAGE_ERROR)
            print(f"下載錯誤: {exc}")
        finally:
            if context.requested.is_set() and job is not None:
                if hasattr(job, 'request_stop'):
                    job.request_stop(context.reason, cleanup=False)
                if hasattr(job, 'cancel_download'):
                    job.cancel_download(cleanup=False)
                waiter = getattr(job, 'wait_stopped', None)
                if callable(waiter) and not waiter(timeout=10.0):
                    raise StopTimeoutError('Download is still stopping; I/O ownership has not been released')
    if context.requested.is_set() and context.reason != StopReason.STORAGE_ERROR:
        return 130
    return result


if __name__ == "__main__":
    consoles_main("")
