#!/usr/bin/env python
# coding: utf-8

import re
import cloudscraper
from uav_downloader.sites.base import *
from bs4 import BeautifulSoup
from uav_downloader.core import config
from uav_downloader.core.http import controlled_get


class SiteCCCDNXYZ(M3U8Crawler):

    def get_url_infos(self):
        htmlfile = controlled_get(cloudscraper.create_scraper(browser=request_headers, delay=10),
            self._url, **config.proxy_request_kwargs())
        if htmlfile.status_code != 200:
            raise Exception(f"Bad url names: {self._url}")
        result = re.search('meta name="keywords" content=.+"', htmlfile.text)
        self._targetName = result[0].split('"')[-2].split(',')[0]
        result = re.search(r'thumbnail.+"', htmlfile.text)
        self._imageUrl = re.sub(r'\\', '', result[0].split('"')[2])
        result = re.search(r'://p.+/',htmlfile.text)
        port = result[0].split('/')[2].split('.')[1]
        self._imageUrl = f"https://p.{port}.cccdn.xyz{self._imageUrl}"
        self._m3u8url = re.sub('t.jpg','v.m3u8',self._imageUrl)

class SiteJableOrg(SiteCCCDNXYZ):
    website_pattern = r'https://www\.jable\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.jable\.org/(\d+)\.html$'

class SiteThisAV(SiteCCCDNXYZ):
    website_pattern = r'https://www\.thisav\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.thisav\.org/(\d+)\.html$'

class SitePigAV(SiteCCCDNXYZ):
    website_pattern = r'https://www\.pigav\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.pigav\.org/(\d+)\.html$'

class SitePorn5F(SiteCCCDNXYZ):
    website_pattern = r'https://www\.porn5f\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.porn5f\.org/(\d+)\.html$'

class Site85Tube(SiteCCCDNXYZ):
    website_pattern = r'https://www\.85tube\.org/\d+\.html'
    website_dirname_pattern = r'https://www\.85tube\.org/(\d+)\.html$'

class Site91Porn(SiteCCCDNXYZ):
    website_pattern = r'https://www\.91porn\.best/\d+\.html'
    website_dirname_pattern = r'https://www\.91porn\.best/(\d+)\.html$'

class SitePornBest(SiteCCCDNXYZ):
    website_pattern = r'https://www\.pornbest\.org/\d+'
    website_dirname_pattern = r'https://www\.pornbest\.org/(\d+)'

##############################################################################################
