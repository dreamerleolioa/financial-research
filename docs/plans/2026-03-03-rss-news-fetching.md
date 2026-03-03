# P2-3 RSS 新聞抓取 Plan

> **狀態：已完成實作**
> 日期：2026-03-03

## 目標

讓系統在 `requires_news_refresh=True` 時，能自動從 RSS 拉新聞，不再依賴手動貼入 `--news-text`。

## 設計決策

### 1. 依賴選擇：stdlib 而非 feedparser

- `feedparser` 未安裝，且加入額外依賴需要評估
- Python stdlib 的 `urllib.request` + `xml.etree.ElementTree` 足以解析標準 RSS 2.0
- 結論：用 stdlib，零新增依賴

### 2. 新資料源：`RssNewsClient`

位置：`data_sources/rss_news_client.py`

```
RawNewsItem(dataclass)
  source: str          # "google-news-rss"
  url: str
  title: str
  published_at: str    # RSS pubDate 原字串
  summary: str         # RSS description

RssNewsClient
  fetch_news(query, max_items=5) -> list[RawNewsItem]
  _parse_rss(raw_xml, max_items) -> list[RawNewsItem]   # 供測試直接呼叫
```

預設 RSS URL：`https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant`

### 3. GraphState 擴充

新增欄位：`raw_news_items: list[dict[str, Any]] | None`

存放 `RawNewsItem` 轉 dict 後的原始清單，供後續 cleaner 或 debug 使用。

### 4. 新節點：`fetch_news_node`

位置：`graph/nodes.py`

行為：
1. 取 `state["symbol"]`，切掉 `.TW` 後綴作為查詢詞（e.g. `"2330"`）
2. 呼叫 `rss_client.fetch_news(query=...)`
3. 將結果（asdict）寫入 `raw_news_items`
4. 取第一篇的 `published_at + title + summary` 合併為 `news_content`，供後續清潔器使用
5. 例外時：回傳空 `raw_news_items` + 累積 `RSS_FETCH_ERROR`

### 5. Graph Routing 更新

`judge` 節點後的路由新增分支：

```
data_sufficient      → analyze
retry >= max_retries → analyze（強制往下）
requires_news_refresh → fetch_news → increment_retry → crawl
otherwise            → increment_retry → crawl
```

`build_graph` 新增可選參數 `rss_client: RssNewsClient | None`，若未傳入則自動建立預設實例。

## 實作後的檔案異動

| 檔案 | 異動 |
|------|------|
| `data_sources/rss_news_client.py` | 新增 |
| `graph/state.py` | 加 `raw_news_items` 欄位 |
| `graph/nodes.py` | 加 `fetch_news_node`，import `RssNewsClient` |
| `graph/builder.py` | 加 `fetch_news` 節點與路由邏輯，`rss_client` 參數 |
| `tests/test_rss_news_client.py` | 新增（6 tests） |
| `tests/test_graph_nodes.py` | 新增 5 tests，更新 `_base_state` |
| `tests/test_graph_state.py` | 新增 `raw_news_items` 測試，更新既有 state |
| `tests/test_graph_builder.py` | 更新 `_initial_state` |

## 驗收

```bash
cd backend && make test
# 30 passed
```
