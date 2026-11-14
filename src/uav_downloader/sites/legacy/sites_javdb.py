#!/usr/bin/env python
# coding: utf-8

import re
import cloudscraper
from uav_downloader.sites.base import *
from bs4 import BeautifulSoup
from uav_downloader.core import config
from uav_downloader.core.http import controlled_get, controlled_request

class SiteJavdbLive(M3U8Crawler):
    website_pattern = r'https://www\.javdb\.live/\d+\.html'
    website_dirname_pattern = r'https://www\.javdb\.live/(\d+)\.html$'

    def get_url_infos(self):
        htmlfile = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
            self._url, **config.proxy_request_kwargs())
        if htmlfile.status_code != 200:
            raise Exception(f"Bad url names: {self._url}")
        result = re.search('meta name="keywords" content=.+"', htmlfile.text)
        self._targetName = result[0].split('"')[-2].split(',')[0]
        result = re.search(r'thumbnail.+"', htmlfile.text)
        self._imageUrl = re.sub(r'\\', '', result[0].split('"')[2])
        result = re.search(r'm3u8\|.+\|src', htmlfile.text, flags=re.I)
        uu = result[0].split('|')
        self._m3u8url = f'https://{uu[-2]}.{uu[-3]}.{uu[-4]}'
        uu = uu[:-4]
        while uu:
            if len(uu) == 1: break;
            self._m3u8url = self._m3u8url + '/' + uu[-1]
            uu.pop(-1)
        self._m3u8url = self._m3u8url + '.m3u8'


class SiteHAnimeXYZ(SiteJavdbLive):
    website_pattern = r'https://www\.hanime\.xyz/\d+\.html'
    website_dirname_pattern = r'https://www\.hanime\.xyz/(\d+)\.html$'

class SitePornTW(SiteJavdbLive):
    website_pattern = r'https://www\.porntw\.com/\d+\.html'
    website_dirname_pattern = r'https://www\.porntw\.com/(\d+)\.html$'

class SitePornJP(SiteJavdbLive):
    website_pattern = r'https://www\.pornjp\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.pornjp\.org/(\d+)\.html$'

class SitePornHK(SiteJavdbLive):
    website_pattern = r'https://www\.pornhk\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.pornhk\.org/(\d+)\.html$'

class SitePornHoHo(SiteJavdbLive):
    website_pattern = r'https://www\.pornhoho\.com/\d+\.html'
    website_dirname_pattern = r'https://www\.pornhoho\.com/(\d+)\.html$'

class SitePornNVR(SiteJavdbLive):
    website_pattern = r'https://www\.pornnvr\.me/\d+\.html'
    website_dirname_pattern = r'https://www\.pornnvr\.me/(\d+)\.html$'

class SiteVideo01(SiteJavdbLive):
    website_pattern = r'https://www\.video01\.org/mp4/.+'
    website_dirname_pattern = r'https://www\.video01\.org/mp4/(.+)'

class SitePornLuLu(SiteJavdbLive):
    website_pattern = r'https://www\.pornlulu\.com/v/.+'
    website_dirname_pattern = r'https://www\.pornlulu\.com/v/(.+)'


##############################################################################################
class SiteMIEN321(M3U8Crawler):
    website_pattern = r'https://www\.mien321\.cc/index\.php/vodplay/.+\.html'
    website_dirname_pattern = r'https://www\.mien321\.cc/index\.php/vodplay/(.+)\.html$'

    def get_url_infos(self):
        htmlfile = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
            self._url, **config.proxy_request_kwargs())
        if htmlfile.status_code != 200:
            raise Exception(f"Bad url names: {self._url}")
        soup = BeautifulSoup(htmlfile.content, 'html.parser')
        self._targetName = soup.find_all('p', class_="group-title")[2].string
        result = re.search(r',"url":".+,"url_next', htmlfile.text)
        if result:
            url_addon = 'https://www.mien321.cc/addons/dplayer/?url=' + result[0].split('"')[3]
            htmlfile_addon = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
                url_addon, **config.proxy_request_kwargs())
            if htmlfile_addon.status_code == 200:
                result = re.search(r'https://.+\.m3u8', htmlfile_addon.text, flags=re.I)
                if result:
                    self._m3u8url = result[0]


##############################################################################################
class SiteAApp11(M3U8Crawler):
    website_pattern = r'https://www\.aapp11\.life/index\.php/vod/play/id/\d+/.+\.html'
    website_dirname_pattern = r'https://www\.aapp11\.life/index\.php/vod/play/id/(\d+)/.+\.html$'

    def get_url_infos(self):
        htmlfile = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
            self._url, **config.proxy_request_kwargs())
        if htmlfile.status_code != 200:
            raise Exception(f"Bad url names: {self._url}")
        soup = BeautifulSoup(htmlfile.content, 'html.parser')
        self._targetName = soup.find_all('div', class_="title")[2].h1.string
        result = re.search(r'url":"%.+"', htmlfile.text)
        if result:
            encode_str = result[0].split('"')[2]
            encode_str = re.sub(r'%', '', encode_str)
            url_addon = 'https://www.aapp11.life/addons/dplayer/?url=' + bytes.fromhex(encode_str).decode('ascii')
            htmlfile_addon = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
                url_addon, **config.proxy_request_kwargs())
            if htmlfile_addon.status_code == 200:
                result = re.search(r'https://.+\.m3u8', htmlfile_addon.text, flags=re.I)
                if result:
                    self._m3u8url = result[0]


##############################################################################################
class SiteSeselah(M3U8Crawler):
    website_pattern = r'https://www\.seselah\.com/v/\d+/.+'
    website_dirname_pattern = r'https://www\.seselah\.com/v/(\d+)/.+'

    def get_url_infos(self):
        htmlfile = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
            self._url, **config.proxy_request_kwargs())
        if htmlfile.status_code != 200:
            raise Exception(f"Bad url names: {self._url}")
        result = re.search(r'og:title".+">', htmlfile.text)
        self._targetName = result[0].split('"')[-2]
        result = re.search(r'og:image".+/>', htmlfile.text)
        self._imageUrl = result[0].split('"')[-2]
        result = re.search(r"unescape\('.+'\)", htmlfile.text)
        if result:
            encode_str = result[0].split("'")[-2]
            encode_str = re.sub(r'%', '', encode_str)
            self._m3u8url = 'https://www.seselah.com/' + bytes.fromhex(encode_str).decode('ascii')


##############################################################################################
class SiteXJISHI(M3U8Crawler):
    website_pattern = r'https://forum\.xjishi\.site/detail\.html\?id=\d+#*'
    website_dirname_pattern = r'https://forum\.xjishi\.site/detail\.html\?id=(\d+)#*'

    def get_url_infos(self):
        url_addon = 'https://forum.xjishi.site/sixvideo/video/relatePage?id=' + self._dirName
        scraper = cloudscraper.create_scraper()
        htmlfile_addon = controlled_request(scraper, 'post',
            url_addon, json={'id': self._dirName}, headers=request_headers,
            **config.proxy_request_kwargs())
        if htmlfile_addon.status_code == 200:
            content = re.search(r'"main":\{"id":.+\}', htmlfile_addon.text)
            if content:
                result_text = content[0]
                result = re.search('localPath":".+"?', result_text)
                if not result: return
                localPath = 'https://www.xvideoy.xyz/videos/' + result[0].split('"')[2] + '/'
                result = re.search('videoTitle":".+"?,', result_text)
                if not result: return
                self._targetName = result[0].split('"')[2]
                result = re.search('localImageFile":".+"?,', result_text)
                if not result: return
                self._imageUrl = localPath + result[0].split('"')[2]
                result = re.search('localVideoFileHigh":".+"?,', result_text)
                if not result: return
                self._m3u8url = localPath + 'hls/' + result[0].split('"')[2]
