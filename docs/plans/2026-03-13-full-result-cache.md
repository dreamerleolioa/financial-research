# Full Result Cache Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `stock_analysis_cache` 加入 `full_result JSONB` 欄位，讓快取命中時可回傳與第一次分析完全相同的完整 `AnalyzeResponse`，而非只有精簡欄位。

**Architecture:** 新增 Alembic migration 加欄位 → 更新 ORM model → `upsert_analysis_cache` 多存一個 key → `_build_response_from_cache` 改從 `full_result` 還原完整 response。快取邏輯（`_handle_cache_hit`、盤中/收盤判斷）維持不變。

**Tech Stack:** Python, SQLAlchemy (mapped_column / JSONB), Alembic, FastAPI, pytest

---

### Task 1: Alembic migration — 新增 `full_result` 欄位

**Files:**
- Create: `backend/alembic/versions/<new_revision>_add_full_result_to_cache.py`

**Step 1: 產生新的 migration 檔**

```bash
cd backend
alembic revision --autogenerate -m "add_full_result_to_stock_analysis_cache"
```

Expected: 在 `alembic/versions/` 產生新檔，`down_revision` 應指向 `4e09fb1d0f78`。

**Step 2: 檢查自動產生的 migration 內容**

autogenerate 可能沒有正確偵測到欄位（因為 model 尚未修改），先確認檔案內容，如果 upgrade() 是空的，手動補上：

```python
def upgrade() -> None:
    op.add_column(
        "stock_analysis_cache",
        sa.Column("full_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stock_analysis_cache", "full_result")
```

確認 import 區塊有 `from sqlalchemy.dialects import postgresql`。

**Step 3: 執行 migration，確認 schema 更新**

```bash
cd backend
alembic upgrade head
```

Expected: 印出 `Running upgrade 4e09fb1d0f78 -> <new_revision>`，無 error。

**Step 4: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat: add full_result column to stock_analysis_cache"
```

---

### Task 2: 更新 ORM Model

**Files:**
- Modify: `backend/src/ai_stock_sentinel/db/models.py:75-95`

**Step 1: 寫失敗測試**

開啟 `backend/tests/test_db_models.py`，在 `StockAnalysisCache` 相關測試後加入：

```python
def test_stock_analysis_cache_has_full_result_column() -> None:
    from ai_stock_sentinel.db.models import StockAnalysisCache
    assert hasattr(StockAnalysisCache, "full_result")
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend
pytest tests/test_db_models.py::test_stock_analysis_cache_has_full_result_column -v
```

Expected: `FAILED` — `AttributeError` 或 assert 失敗。

**Step 3: 在 ORM model 加欄位**

在 `db/models.py` 的 `StockAnalysisCache` class，`updated_at` 行之前加入：

```python
full_result:        Mapped[dict | None]  = mapped_column(JSONB, nullable=True)
```

**Step 4: 執行測試，確認通過**

```bash
cd backend
pytest tests/test_db_models.py::test_stock_analysis_cache_has_full_result_column -v
```

Expected: `PASSED`

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/db/models.py backend/tests/test_db_models.py
git commit -m "feat: add full_result mapped column to StockAnalysisCache"
```

---

### Task 3: `upsert_analysis_cache` 儲存完整 result

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:177-214`

目前 `upsert_analysis_cache` 接受一個 `data: dict`，裡面沒有 `full_result`。需要：
1. SQL INSERT/UPDATE 加上 `full_result` 欄位
2. 呼叫端傳入 `full_result` 值

**Step 1: 寫失敗測試**

在 `backend/tests/test_api.py` 尾部新增：

```python
# ---------------------------------------------------------------------------
# Full result cache persistence
# ---------------------------------------------------------------------------

def test_upsert_analysis_cache_stores_full_result(tmp_path) -> None:
    """upsert_analysis_cache should persist full_result when provided."""
    from unittest.mock import MagicMock, call
    from ai_stock_sentinel.api import upsert_analysis_cache

    db = MagicMock()
    data = {
        "symbol": "2330.TW",
        "signal_confidence": 55,
        "action_tag": "neutral",
        "recommended_action": "觀望",
        "indicators": {},
        "final_verdict": "分析結果",
        "is_final": False,
        "full_result": {"snapshot": {"symbol": "2330.TW"}, "analysis": "分析結果"},
    }

    upsert_analysis_cache(db, data)

    # Verify db.execute was called (SQL executed)
    db.execute.assert_called_once()
    # Verify the SQL params include full_result
    call_kwargs = db.execute.call_args
    params = call_kwargs[0][1]  # second positional arg is the params dict
    assert "full_result" in params
    assert params["full_result"] is not None
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend
pytest tests/test_api.py::test_upsert_analysis_cache_stores_full_result -v
```

Expected: `FAILED` — `full_result` 不在 params。

**Step 3: 修改 `upsert_analysis_cache` SQL 與 params**

在 [api.py:180-213](../src/ai_stock_sentinel/api.py) 的 SQL 中加入 `full_result`：

INSERT 欄位列表加 `full_result,`，VALUES 加 `CAST(:full_result AS jsonb),`，ON CONFLICT DO UPDATE SET 加 `full_result = EXCLUDED.full_result,`。

params dict 加：
```python
"full_result": json.dumps(data.get("full_result") or {}),
```

最終 SQL 結構：

```python
db.execute(
    text("""
        INSERT INTO stock_analysis_cache (
            symbol, record_date, signal_confidence, action_tag,
            recommended_action, indicators, final_verdict,
            prev_action_tag, prev_confidence, is_final, full_result, updated_at
        ) VALUES (
            :symbol, CURRENT_DATE, :signal_confidence, :action_tag,
            :recommended_action, CAST(:indicators AS jsonb), :final_verdict,
            (SELECT action_tag FROM stock_analysis_cache
             WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
            (SELECT signal_confidence FROM stock_analysis_cache
             WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
            :is_final, CAST(:full_result AS jsonb), NOW()
        )
        ON CONFLICT (symbol, record_date) DO UPDATE SET
            signal_confidence  = EXCLUDED.signal_confidence,
            action_tag         = EXCLUDED.action_tag,
            recommended_action = EXCLUDED.recommended_action,
            indicators         = EXCLUDED.indicators,
            final_verdict      = EXCLUDED.final_verdict,
            is_final           = EXCLUDED.is_final,
            full_result        = EXCLUDED.full_result,
            updated_at         = NOW()
    """),
    {
        "symbol":             data.get("symbol"),
        "signal_confidence":  data.get("signal_confidence"),
        "action_tag":         data.get("action_tag"),
        "recommended_action": data.get("recommended_action"),
        "indicators":         json.dumps(data.get("indicators") or {}),
        "final_verdict":      data.get("final_verdict"),
        "is_final":           data.get("is_final", False),
        "full_result":        json.dumps(data.get("full_result") or {}),
    }
)
```

**Step 4: 執行測試，確認通過**

```bash
cd backend
pytest tests/test_api.py::test_upsert_analysis_cache_stores_full_result -v
```

Expected: `PASSED`

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_api.py
git commit -m "feat: persist full_result in upsert_analysis_cache"
```

---

### Task 4: 呼叫端傳入 `full_result`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:568-583` (analyze endpoint)
- Modify: `backend/src/ai_stock_sentinel/api.py:686-701` (analyze/position endpoint)

兩個 endpoint 在呼叫 `upsert_analysis_cache` 時，`data` dict 需加入 `full_result`。

`full_result` 的值就是 `result`（`graph.invoke` 的完整回傳），但要先把它轉成可序列化的格式（去除 dataclass，保留 dict）。可用現有的 `_build_response` 得到 `AnalyzeResponse` 再呼叫 `.model_dump()`。

**Step 1: 寫失敗測試**

在 `test_api.py` 新增：

```python
def test_analyze_cache_is_called_with_full_result(monkeypatch) -> None:
    """POST /analyze should pass full_result to upsert_analysis_cache."""
    from unittest.mock import MagicMock, patch
    import ai_stock_sentinel.api as api_module

    graph = _make_graph(
        {
            "snapshot": {"symbol": "2330.TW", "current_price": 100.0},
            "analysis": "分析結果",
            "signal_confidence": 55,
            "action_plan_tag": "neutral",
            "errors": [],
        }
    )

    captured = {}

    def fake_upsert(db, data):
        captured["data"] = data

    monkeypatch.setattr(api_module, "upsert_analysis_cache", fake_upsert)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)

    client = _client_with_graph(graph)
    client.post("/analyze", json={"symbol": "2330.TW"})

    assert "full_result" in captured.get("data", {})
    full = captured["data"]["full_result"]
    assert full.get("analysis") == "分析結果"
    assert full.get("snapshot", {}).get("symbol") == "2330.TW"
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend
pytest tests/test_api.py::test_analyze_cache_is_called_with_full_result -v
```

Expected: `FAILED`

**Step 3: 修改兩個 endpoint 的 `upsert_analysis_cache` 呼叫**

在 `/analyze` endpoint（約 line 569）和 `/analyze/position` endpoint（約 line 687）的 `upsert_analysis_cache` 呼叫的 data dict 加入：

```python
"full_result": _build_response(result).model_dump(),
```

注意：要放在 `response = _build_response(result)` **之前**，或者直接 inline：

```python
_response = _build_response(result)
upsert_analysis_cache(db, {
    "symbol":             payload.symbol,
    "signal_confidence":  result.get("signal_confidence"),
    "action_tag":         result.get("action_plan_tag"),
    "recommended_action": result.get("recommended_action"),
    "indicators":         _extract_indicators(result),
    "final_verdict":      result.get("analysis"),
    "is_final":           is_final,
    "full_result":        _response.model_dump(),
})
# ...
response = _response
response.is_final = is_final
response.intraday_disclaimer = INTRADAY_DISCLAIMER if not is_final else None
return response
```

同樣修改 `/analyze/position`。

**Step 4: 執行測試**

```bash
cd backend
pytest tests/test_api.py::test_analyze_cache_is_called_with_full_result -v
```

Expected: `PASSED`

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_api.py
git commit -m "feat: pass full_result to upsert_analysis_cache in both endpoints"
```

---

### Task 5: 快取命中時從 `full_result` 還原完整 response

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py:126-174` (`_handle_cache_hit`, `_build_response_from_cache`)

目前 `_handle_cache_hit` 回傳 `CachedAnalyzeResponse`（精簡），`_build_response_from_cache` 再把它組成只有 5 個欄位的 `AnalyzeResponse`。

新邏輯：若 `cache.full_result` 有值，直接用 `AnalyzeResponse(**cache.full_result)` 還原，再覆寫 `is_final` 和 `intraday_disclaimer`。

**Step 1: 寫失敗測試**

```python
def test_cache_hit_returns_full_result_fields(monkeypatch) -> None:
    """When cache has full_result, /analyze should return all fields."""
    from unittest.mock import MagicMock, patch
    import ai_stock_sentinel.api as api_module
    from datetime import date

    full = {
        "snapshot": {"symbol": "2330.TW", "current_price": 1865.0},
        "analysis": "完整分析內容",
        "signal_confidence": 37,
        "action_plan_tag": "neutral",
        "news_display_items": [{"title": "新聞標題"}],
        "fundamental_data": {"pe_ratio": 28.1},
        "is_final": False,
        "intraday_disclaimer": None,
        "errors": [],
    }

    cache = MagicMock()
    cache.symbol = "2330.TW"
    cache.is_final = False
    cache.full_result = full
    cache.signal_confidence = 37
    cache.action_tag = "neutral"
    cache.recommended_action = None
    cache.final_verdict = "完整分析內容"
    cache.indicators = {}

    monkeypatch.setattr(api_module, "get_analysis_cache", lambda db, symbol: cache)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)

    graph = _make_graph({})  # should not be invoked
    client = _client_with_graph(graph)
    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] == "完整分析內容"
    assert body["fundamental_data"] == {"pe_ratio": 28.1}
    assert body["news_display_items"] == [{"title": "新聞標題"}]
    assert body["snapshot"]["symbol"] == "2330.TW"
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend
pytest tests/test_api.py::test_cache_hit_returns_full_result_fields -v
```

Expected: `FAILED` — `fundamental_data` 和 `news_display_items` 不符。

**Step 3: 修改 `_build_response_from_cache`**

將現有函式改為：

```python
def _build_response_from_cache(
    hit: CachedAnalyzeResponse,
    symbol: str,
    full_result: dict | None = None,
) -> AnalyzeResponse:
    """把快取命中的結果轉成 AnalyzeResponse。

    若 full_result 存在，直接用它還原完整欄位；
    否則 fallback 到精簡欄位（舊資料相容）。
    """
    if full_result:
        resp = AnalyzeResponse(**full_result)
        resp.is_final = hit.is_final
        resp.intraday_disclaimer = hit.intraday_disclaimer
        return resp
    return AnalyzeResponse(
        snapshot={},
        analysis=hit.final_verdict or "",
        signal_confidence=int(hit.signal_confidence) if hit.signal_confidence is not None else None,
        action_plan_tag=hit.action_tag,
        is_final=hit.is_final,
        intraday_disclaimer=hit.intraday_disclaimer,
    )
```

同時修改兩個 endpoint 中呼叫 `_build_response_from_cache` 的地方，傳入 `full_result=cache.full_result`：

```python
return _build_response_from_cache(hit, payload.symbol, full_result=cache.full_result)
```

**Step 4: 執行測試**

```bash
cd backend
pytest tests/test_api.py::test_cache_hit_returns_full_result_fields -v
```

Expected: `PASSED`

**Step 5: 執行全部測試，確認沒有 regression**

```bash
cd backend
pytest tests/test_api.py -v
```

Expected: 所有既有測試 `PASSED`（新測試也 PASSED）。

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_api.py
git commit -m "feat: restore full AnalyzeResponse from cache full_result on cache hit"
```

---

### Task 6: 回歸測試 + 收尾

**Step 1: 執行完整 test suite**

```bash
cd backend
pytest --tb=short -q
```

Expected: 所有測試通過，無 error。

**Step 2: 確認 migration chain 正確**

```bash
cd backend
alembic history --verbose
```

確認 chain 是：`initial → fix_timezone → add_stock_raw_data → add_full_result`，無 branch。

**Step 3: Final commit（若有未 commit 的零散修改）**

```bash
git status
# 若 clean 則不需要額外 commit
```
