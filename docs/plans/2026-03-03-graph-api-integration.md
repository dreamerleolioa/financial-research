# P2-4 將 Graph 接進 `/analyze` API Plan

> **狀態：已完成實作**
> 日期：2026-03-03

## 目標

讓 `POST /analyze` 真正走 LangGraph 回圈（judge → fetch_news → retry），取代舊的 `StockCrawlerAgent` 線性流程。

## 設計決策

### 1. Dependency 注入方式：`get_graph` 取代 `get_agent`

- 舊做法：FastAPI dependency `get_agent() -> StockCrawlerAgent`，測試用 `DummyAgent` 覆蓋
- 新做法：FastAPI dependency `get_graph()`，回傳 `build_graph()` 產出的 compiled graph
- 測試覆蓋改為 mock compiled graph（`graph.invoke.return_value = {...}`），不需要 Dummy class

### 2. `build_graph_deps()` 放在 `main.py`

- `api.py` 不直接處理 LLM 初始化或環境設定，統一委託 `main.py`
- `build_graph_deps()` 回傳 `(crawler, analyzer, rss_client)` tuple，供 `get_graph()` 組裝

### 3. 初始 GraphState 組裝

`/analyze` handler 負責從 request payload 組成初始 state：

```python
{
    "symbol": payload.symbol,
    "news_content": payload.news_text,
    "snapshot": None,
    "analysis": None,
    "cleaned_news": None,
    "raw_news_items": None,
    "data_sufficient": False,
    "retry_count": 0,
    "errors": [],
    "requires_news_refresh": False,
    "requires_fundamental_update": False,
}
```

### 4. `AnalyzeResponse` 欄位對應

| graph state key | AnalyzeResponse 欄位 | 說明 |
|---|---|---|
| `snapshot` | `snapshot` | 缺失時補空 dict + MISSING_SNAPSHOT error |
| `analysis` | `analysis` | 缺失時補空字串 + MISSING_ANALYSIS error |
| `cleaned_news` | `cleaned_news` | 直接傳遞，可為 None |
| `errors` | `errors` | graph 執行期間累積的錯誤 |
| `raw_news_items` | — | 不對外暴露（純 graph 內部狀態） |

### 5. 錯誤處理

- graph 執行期間累積的 `errors`（list[dict]）映射成 `ErrorDetail`，一併回傳
- graph 拋出未預期例外：捕捉後回傳 `ANALYZE_RUNTIME_ERROR`
- snapshot / analysis 缺失：個別附加對應 error code

## 實作後的檔案異動

| 檔案 | 異動 |
|------|------|
| `main.py` | 新增 `build_graph_deps()`，保留原 `build_agent()` 與 CLI |
| `api.py` | 移除 `StockCrawlerAgent`/`get_agent`，改用 `get_graph`；handler 改呼叫 `graph.invoke()` |
| `tests/test_api.py` | 全部重寫：改 mock compiled graph，新增 8 個測試 |

## 驗收

```bash
cd backend && PYTHONPATH=src ./venv/bin/python -m pytest tests/ -v
# 33 passed
```
