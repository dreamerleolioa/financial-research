from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class RawNewsItem:
    source: str
    url: str
    title: str
    published_at: str
    summary: str


_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


class RssNewsClient:
    """透過 RSS 抓取財經新聞（預設使用 Google News RSS）。"""

    def __init__(self, rss_url_template: str = _GOOGLE_NEWS_RSS, timeout: int = 10) -> None:
        self._rss_url_template = rss_url_template
        self._timeout = timeout

    def fetch_news(self, query: str, max_items: int = 5) -> list[RawNewsItem]:
        """指定查詢字串，回傳至多 max_items 筆新聞。"""
        url = self._rss_url_template.format(query=urllib.request.quote(query))
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                raw_xml = resp.read()
        except urllib.error.URLError:
            return []

        return self._parse_rss(raw_xml, max_items=max_items)

    def _parse_rss(self, raw_xml: bytes, max_items: int) -> list[RawNewsItem]:
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        items: list[RawNewsItem] = []
        for item in channel.findall("item"):
            if len(items) >= max_items:
                break

            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()

            items.append(
                RawNewsItem(
                    source="google-news-rss",
                    url=link,
                    title=title,
                    published_at=pub_date,
                    summary=description,
                )
            )

        return items
