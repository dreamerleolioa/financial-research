# Backfill Yesterday Indicators Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在每次呼叫 `/analyze` 或 `/analyze/position` 時，自動檢查昨日快取是否為盤中未定稿（`is_final=False`），若是則用 yfinance 補抓昨日收盤技術指標並更新 `indicators`、`is_final=True`，確保 `history_loader` 拿到準確的收盤數據。

**Architecture:** 新增 `backfill_yesterday_indicators(db, symbol)` 函式於 `services/history_loader.py`（與 `load_yesterday_context` 同檔），在兩個 endpoint 的 `graph.invoke` **之前**呼叫。yfinance 用 `history(period="5d", interval="1d")` 取昨日那列計算指標，只更新 `indicators` 與 `is_final`，不動 `action_tag`/`final_verdict`。

**Tech Stack:** Python, SQLAlchemy (`text()`), yfinance, pytest

---

### Task 1: 實作 `backfill_yesterday_indicators`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/services/history_loader.py`
- Test: `backend/tests/test_history_loader.py`

**Step 1: 寫失敗測試**

開啟 `backend/tests/test_history_loader.py`，在既有測試後加入：

```python
def test_backfill_yesterday_indicators_updates_is_final(monkeypatch) -> None:
    """昨日 is_final=False 時，backfill 應更新 indicators 並設 is_final=True。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None  # 預設昨日無資料

    # 製造昨日 is_final=False 的快取
    cache = MagicMock()
    cache.is_final = False
    cache.symbol = "2330.TW"

    # 第一次 execute 查昨日快取，回傳 cache
    db.execute.return_value.scalar_one_or_none.return_value = cache

    # mock yfinance 回傳含昨日收盤的 history
    import pandas as pd
    from datetime import date, timedelta
    yesterday = date.today() - timedelta(days=1)
    fake_history = pd.DataFrame(
        {"Close": [185.0, 187.0], "Volume": [10000, 12000]},
        index=pd.to_datetime([str(yesterday - timedelta(days=1)), str(yesterday)]),
    )

    with patch("ai_stock_sentinel.services.history_loader.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = fake_history
        backfill_yesterday_indicators(db, "2330.TW")

    db.execute.assert_called()  # 有執行 UPDATE SQL
    db.commit.assert_called_once()


def test_backfill_yesterday_indicators_skips_when_already_final(monkeypatch) -> None:
    """昨日 is_final=True 時，backfill 應跳過，不執行任何 DB 寫入。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    cache = MagicMock()
    cache.is_final = True
    db.execute.return_value.scalar_one_or_none.return_value = cache

    backfill_yesterday_indicators(db, "2330.TW")

    # 只有一次 execute（查詢），沒有 commit
    assert db.execute.call_count == 1
    db.commit.assert_not_called()


def test_backfill_yesterday_indicators_skips_when_no_cache() -> None:
    """昨日無快取時，backfill 應直接 return，不呼叫 yfinance。"""
    from unittest.mock import MagicMock, patch
    from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("ai_stock_sentinel.services.history_loader.yf.Ticker") as mock_ticker:
        backfill_yesterday_indicators(db, "2330.TW")
        mock_ticker.assert_not_called()

    db.commit.assert_not_called()
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend
pytest tests/test_history_loader.py::test_backfill_yesterday_indicators_updates_is_final tests/test_history_loader.py::test_backfill_yesterday_indicators_skips_when_already_final tests/test_history_loader.py::test_backfill_yesterday_indicators_skips_when_no_cache -v
```

Expected: `FAILED` — `ImportError: cannot import name 'backfill_yesterday_indicators'`

**Step 3: 實作 `backfill_yesterday_indicators`**

在 `backend/src/ai_stock_sentinel/services/history_loader.py` 加入（在 `load_yesterday_context` 之後）：

```python
import json
from datetime import timedelta

import yfinance as yf

from sqlalchemy import text


def _compute_indicators_from_history(history) -> dict:
    """從 yfinance history DataFrame 計算技術指標摘要。"""
    if history.empty or "Close" not in history.columns:
        return {}
    closes = history["Close"].dropna().tolist()
    if not closes:
        return {}

    def _ma(n: int) -> float | None:
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    last_close = float(closes[-1])
    ma5  = _ma(5)
    ma20 = _ma(20)
    ma60 = _ma(60)

    # RSI-14
    rsi14: float | None = None
    if len(closes) >= 15:
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        if avg_loss == 0:
            rsi14 = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi14 = round(100 - 100 / (1 + rs), 2)

    return {
        "ma5":         ma5,
        "ma20":        ma20,
        "ma60":        ma60,
        "rsi_14":      rsi14,
        "close_price": last_close,
    }


def backfill_yesterday_indicators(db: Session, symbol: str) -> None:
    """若昨日快取為盤中未定稿（is_final=False），補抓收盤指標並更新。

    只更新 indicators 與 is_final，不動 action_tag/final_verdict。
    """
    yesterday = date.today() - timedelta(days=1)
    row = db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == yesterday,
        )
    ).scalar_one_or_none()

    if row is None or row.is_final:
        return

    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d", interval="1d")
        # 只取昨日那列
        history.index = history.index.normalize()
        yesterday_ts = pd.Timestamp(yesterday)
        if yesterday_ts in history.index:
            history = history.loc[:yesterday_ts]
        indicators = _compute_indicators_from_history(history)
    except Exception:
        return  # yfinance 失敗時靜默跳過，不影響主流程

    if not indicators:
        return

    db.execute(
        text("""
            UPDATE stock_analysis_cache
            SET indicators = CAST(:indicators AS jsonb),
                is_final   = TRUE,
                updated_at = NOW()
            WHERE symbol      = :symbol
              AND record_date = :record_date
        """),
        {
            "indicators":  json.dumps(indicators),
            "symbol":      symbol,
            "record_date": yesterday.isoformat(),
        },
    )
    db.commit()
```

同時在檔案頂部加入缺少的 imports：

```python
import json
from datetime import timedelta

import pandas as pd
import yfinance as yf
from sqlalchemy import text
```

**Step 4: 執行測試，確認通過**

```bash
cd backend
pytest tests/test_history_loader.py::test_backfill_yesterday_indicators_updates_is_final tests/test_history_loader.py::test_backfill_yesterday_indicators_skips_when_already_final tests/test_history_loader.py::test_backfill_yesterday_indicators_skips_when_no_cache -v
```

Expected: 3 × `PASSED`

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/services/history_loader.py backend/tests/test_history_loader.py
git commit -m "feat: add backfill_yesterday_indicators to patch intraday cache at day boundary"
```

---

### Task 2: 在兩支 endpoint 呼叫 `backfill_yesterday_indicators`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Test: `backend/tests/test_api.py`

呼叫時機：**在 `graph.invoke` 之前、`get_analysis_cache` 之後**（快取未命中才會到這裡）。

**Step 1: 寫失敗測試**

在 `backend/tests/test_api.py` 尾部加入：

```python
# ---------------------------------------------------------------------------
# Backfill yesterday indicators
# ---------------------------------------------------------------------------

def test_analyze_calls_backfill_yesterday_indicators(monkeypatch) -> None:
    """POST /analyze 應在 graph.invoke 之前呼叫 backfill_yesterday_indicators。"""
    import ai_stock_sentinel.api as api_module
    from dataclasses import asdict

    called = {}

    def fake_backfill(db, symbol):
        called["symbol"] = symbol

    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", fake_backfill)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)

    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "errors": [],
    })
    client = _client_with_graph(graph)
    client.post("/analyze", json={"symbol": "2330.TW"})

    assert called.get("symbol") == "2330.TW"
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend
pytest tests/test_api.py::test_analyze_calls_backfill_yesterday_indicators -v
```

Expected: `FAILED` — `AttributeError: module 'ai_stock_sentinel.api' has no attribute 'backfill_yesterday_indicators'`

**Step 3: 修改 `api.py`**

在 `api.py` 頂部 import 區加入：

```python
from ai_stock_sentinel.services.history_loader import backfill_yesterday_indicators
```

在 `/analyze` endpoint 的快取未命中路徑（`get_analysis_cache` 之後、`initial_state` 建立之前）加入：

```python
    backfill_yesterday_indicators(db, payload.symbol)
```

同樣在 `/analyze/position` endpoint 的對應位置加入相同一行。

**Step 4: 執行測試，確認通過**

```bash
cd backend
pytest tests/test_api.py::test_analyze_calls_backfill_yesterday_indicators -v
```

Expected: `PASSED`

**Step 5: 執行完整 test suite，確認無 regression**

```bash
cd backend
pytest tests/test_api.py tests/test_history_loader.py -q
```

Expected: 全部 `PASSED`

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "feat: call backfill_yesterday_indicators before graph.invoke in analyze endpoints"
```

---

### Task 3: 回歸測試

**Step 1: 執行完整 test suite**

```bash
cd backend
pytest --tb=short -q
```

Expected: 全部通過，無 error。
