from __future__ import annotations

from unittest.mock import MagicMock, patch

from ai_stock_sentinel.data_sources.rss_news_client import RawNewsItem, RssNewsClient


_SAMPLE_RSS_STR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>TSMC revenue up 20% YoY</title>
      <link>https://example.com/news/1</link>
      <pubDate>Mon, 03 Mar 2026 08:00:00 GMT</pubDate>
      <description>TSMC reported Feb revenue, up 20% YoY, beating market expectations.</description>
    </item>
    <item>
      <title>Foreign investors buy 5,000 lots of TSMC</title>
      <link>https://example.com/news/2</link>
      <pubDate>Mon, 03 Mar 2026 07:00:00 GMT</pubDate>
      <description>Foreign investors bought TSMC for 3 consecutive days, totalling 5,000 lots.</description>
    </item>
  </channel>
</rss>
"""

_SAMPLE_RSS: bytes = _SAMPLE_RSS_STR.encode("utf-8")


def _make_client() -> RssNewsClient:
    return RssNewsClient()


def test_parse_rss_returns_correct_count() -> None:
    client = _make_client()
    items = client._parse_rss(_SAMPLE_RSS, max_items=5)
    assert len(items) == 2


def test_parse_rss_respects_max_items() -> None:
    client = _make_client()
    items = client._parse_rss(_SAMPLE_RSS, max_items=1)
    assert len(items) == 1


def test_parse_rss_item_fields() -> None:
    client = _make_client()
    items = client._parse_rss(_SAMPLE_RSS, max_items=5)
    first = items[0]
    assert isinstance(first, RawNewsItem)
    assert first.source == "google-news-rss"
    assert first.title == "TSMC revenue up 20% YoY"
    assert first.url == "https://example.com/news/1"
    assert "2026" in first.published_at
    assert "20%" in first.summary


def test_parse_rss_returns_empty_on_invalid_xml() -> None:
    client = _make_client()
    items = client._parse_rss(b"not-xml-at-all!!!", max_items=5)
    assert items == []


def test_fetch_news_returns_empty_on_url_error() -> None:
    import urllib.error

    client = _make_client()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        items = client.fetch_news(query="2330")
    assert items == []


def test_fetch_news_calls_parse_with_response_body() -> None:
    client = _make_client()
    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.read.return_value = _SAMPLE_RSS

    with patch("urllib.request.urlopen", return_value=mock_response):
        items = client.fetch_news(query="TSMC", max_items=5)

    assert len(items) == 2
    assert items[0].title == "TSMC revenue up 20% YoY"
