# Phase 8 邏輯強化 Implementation Plan

> 狀態：待執行
> 預計執行日期：2026-03-16

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 將系統從孤立的當日診斷升級為具備歷史記憶的連續敘事分析，並建立勝率回測腳本為未來信心分數校準奠定基礎。

**Architecture:** `history_loader.py`（Phase 7 已建立）的昨日上下文注入 LangGraph GraphState，再透過新的 `prev_context` 欄位傳入 LLM Prompt，產出包含「訊號轉向分析」的 `final_verdict`。依 review-spec v1.7，兩支分析端點在讀取昨日上下文前都必須先執行 `backfill_yesterday_indicators(db, symbol)`，若昨日快取仍是盤中未定稿（`is_final=False`）則先補成收盤版指標，再由 `load_yesterday_context` 從 `stock_analysis_cache` 讀取（跨使用者共用，非持倉查詢也有記錄，資料更完整）。勝率回測腳本獨立為 CLI 工具，讀取 `daily_analysis_log` 並以 yfinance 驗證實際漲跌，輸出 Pearson 相關性報告。

**Tech Stack:** Python、SQLAlchemy AsyncSession、LangGraph GraphState、yfinance、scipy（pearsonr）

**前置依賴:** Phase 7 完成（`daily_analysis_log` 已有數據、`history_loader.py` 已實作）。注意：`history_loader.py` 的昨日上下文查詢已改為從 `stock_analysis_cache` 讀取（按 symbol 查，不分使用者），確保非持倉查詢也能取得昨日上下文。

---

## Task 1: GraphState 加入 `prev_context` 欄位

**Files:**

- Modify: `backend/src/ai_stock_sentinel/graph/state.py`
- Modify: `backend/tests/test_graph_state.py`

**Step 1: 讀取現有 test_graph_state.py**

```bash
cat backend/tests/test_graph_state.py
```

**Step 2: 在 state.py 新增 prev_context 欄位**

在 `GraphState` TypedDict 末尾加入：

```python
# --- History Context (from stock_analysis_cache, injected before LLM call) ---
prev_context: dict[str, Any] | None   # load_yesterday_context() 的回傳值
```

**Step 3: 寫測試確認欄位存在**

在 `backend/tests/test_graph_state.py` 加入：

```python
def test_prev_context_field_exists():
    """GraphState 應包含 prev_context 欄位。"""
    from ai_stock_sentinel.graph.state import GraphState
    import typing
    hints = typing.get_type_hints(GraphState)
    assert "prev_context" in hints
```

**Step 4: 執行測試**

```bash
cd backend && pytest tests/test_graph_state.py -v
```

Expected: PASSED

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/graph/state.py backend/tests/test_graph_state.py
git commit -m "feat: add prev_context field to GraphState for history injection"
```

---

## Task 2: 兩支分析路由注入昨日上下文

**Files:**

- Modify: `backend/src/ai_stock_sentinel/api.py`
- Modify: `backend/tests/test_api.py`

**Step 1: 寫失敗測試**

在 `backend/tests/test_api.py` 加入：

```python
from unittest.mock import patch, AsyncMock

def test_analyze_position_injects_prev_context(mock_graph_with_state):
    """analyze/position 應在 graph.invoke 前先 backfill，再注入 prev_context。"""
    prev_ctx = {
        "prev_action_tag": "Hold",
        "prev_confidence": 61.5,
        "prev_rsi": 65.2,
        "prev_ma_alignment": "bullish",
    }

    with patch("ai_stock_sentinel.api.backfill_yesterday_indicators") as mock_backfill, \
         patch("ai_stock_sentinel.api.load_yesterday_context", new_callable=AsyncMock) as mock_loader:
        mock_loader.return_value = prev_ctx
        # graph.invoke 收到的 initial_state 應包含 prev_context
        client = TestClient(api.app)
        resp = client.post("/analyze/position", json={
            "symbol": "2330.TW",
            "entry_price": 950.0,
        })
        assert resp.status_code == 200
        mock_backfill.assert_called_once()
        call_args = mock_graph_with_state.invoke.call_args[0][0]
        assert call_args["prev_context"] == prev_ctx


def test_analyze_injects_prev_context(mock_graph_with_state):
    """analyze 也應在 graph.invoke 前先 backfill，再注入 prev_context。"""
    prev_ctx = {
        "prev_action_tag": "Trim",
        "prev_confidence": 74.0,
        "prev_rsi": 73.8,
        "prev_ma_alignment": "bullish",
    }

    with patch("ai_stock_sentinel.api.backfill_yesterday_indicators") as mock_backfill, \
         patch("ai_stock_sentinel.api.load_yesterday_context", new_callable=AsyncMock) as mock_loader:
        mock_loader.return_value = prev_ctx
        client = TestClient(api.app)
        resp = client.post("/analyze", json={"symbol": "2330.TW"})
        assert resp.status_code == 200
        mock_backfill.assert_called_once()
        call_args = mock_graph_with_state.invoke.call_args[0][0]
        assert call_args["prev_context"] == prev_ctx
```

**Step 2: 在 api.py 修改兩支 analyze 路由**

```python
# api.py 新增 import
from ai_stock_sentinel.services.history_loader import (
    backfill_yesterday_indicators,
    load_yesterday_context,
)

# analyze_position / analyze 路由：在 graph.invoke 前插入
@app.post("/analyze/position", response_model=AnalyzeResponse)
async def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AnalyzeResponse:
    # 若昨日快取仍是盤中版，先補成收盤指標，避免 history_loader 讀到未定稿資料
    backfill_yesterday_indicators(db, payload.symbol)

    # 從 DB 讀取昨日上下文（Tool Use 原則：數值必須從 DB 讀取，不由 LLM 推斷）
    prev_context = await load_yesterday_context(payload.symbol, db)

    initial_state: GraphState = {
        # ... 原有欄位不變 ...
        "prev_context": prev_context,   # 新增
    }
    # ... 後續 graph.invoke 邏輯不變 ...


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AnalyzeResponse:
    backfill_yesterday_indicators(db, payload.symbol)
    prev_context = await load_yesterday_context(payload.symbol, db)

    initial_state: GraphState = {
        # ... 原有欄位不變 ...
        "prev_context": prev_context,
    }
```

**Step 3: 執行所有 API 測試**

```bash
cd backend && pytest tests/test_api.py -v
```

Expected: 全部 PASSED

**Step 4: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_api.py
git commit -m "feat: inject yesterday context into both analyze routes after backfill"
```

---

## Task 3: Prompt 升級——加入訊號轉向分析段落

**前置說明：`history_loader.py` 昨日上下文來源**

`load_yesterday_context` 查詢目標已從 `DailyAnalysisLog` 改為 `StockAnalysisCache`（查 `stock_analysis_cache` 表，按 symbol 查，不含 user_id）。原因：`daily_analysis_log` 只有持倉使用者才有紀錄，非持倉查詢不會有昨日記錄；改為查 `stock_analysis_cache` 後，跨使用者共用，資料更完整。

實作如下：

```python
# backend/src/ai_stock_sentinel/services/history_loader.py
from ai_stock_sentinel.db.models import StockAnalysisCache  # 查 stock_analysis_cache，非 daily_analysis_log

async def load_yesterday_context(symbol: str, db: AsyncSession) -> dict | None:
    """從 stock_analysis_cache 讀取昨日上下文（跨使用者共用，資料更完整）。"""
    yesterday = date.today() - timedelta(days=1)
    result = await db.execute(
        select(StockAnalysisCache).where(   # 查 stock_analysis_cache
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == yesterday,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    indicators = row.indicators or {}
    return {
        "prev_action_tag":   row.action_tag,
        "prev_confidence":   float(row.signal_confidence) if row.signal_confidence else None,
        "prev_rsi":          indicators.get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(indicators),
    }
```

測試中 mock 的欄位名稱（`action_tag`、`signal_confidence`、`indicators`）在 `StockAnalysisCache` 和 `DailyAnalysisLog` 都有，mock 邏輯本身不需要修改，但測試 docstring 需更新說明查的是 `stock_analysis_cache`。

---

**Files:**

- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`
- Modify: `backend/tests/test_langchain_analyzer.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/test_langchain_analyzer.py 新增

def test_position_prompt_includes_history_when_prev_context_provided():
    """有昨日上下文時，position prompt 應包含訊號轉向區塊。
    昨日數據來自 stock_analysis_cache（非 daily_analysis_log）。
    """
    from ai_stock_sentinel.analysis.langchain_analyzer import build_position_history_section

    prev = {
        "prev_action_tag":   "Hold",
        "prev_confidence":   61.5,
        "prev_rsi":          65.2,
        "prev_ma_alignment": "bullish",
    }
    section = build_position_history_section(prev)

    assert "昨日建議：Hold" in section
    assert "61.5" in section
    assert "RSI：65.2" in section


def test_position_prompt_empty_when_no_prev_context():
    """無昨日上下文時，history section 應為空字串。
    （stock_analysis_cache 無該日紀錄時 load_yesterday_context 回傳 None）
    """
    from ai_stock_sentinel.analysis.langchain_analyzer import build_position_history_section

    assert build_position_history_section(None) == ""
```

**Step 2: 實作 build_position_history_section**

在 `langchain_analyzer.py` 新增函式：

```python
def build_position_history_section(prev_context: dict | None) -> str:
    """將昨日上下文格式化為 Prompt 的【訊號連續性】區塊。

    所有數值來自 DB 讀取，此函式僅做格式化，不推斷任何數值。
    """
    if prev_context is None:
        return ""
    return (
        f"\n【訊號連續性分析（昨日數據來自 DB，非 LLM 推斷）】\n"
        f"- 昨日建議：{prev_context.get('prev_action_tag', 'N/A')}"
        f"（信心：{prev_context.get('prev_confidence', 'N/A')}）\n"
        f"- 昨日 RSI：{prev_context.get('prev_rsi', 'N/A')}\n"
        f"- 昨日均線排列：{prev_context.get('prev_ma_alignment', 'N/A')}\n"
        f"請在 final_verdict 中說明今日訊號與昨日的連續性或轉向原因。\n"
    )
```

**Step 3: 在兩種分析流程帶入 history section**

修改共用分析 node，或同步修改 `analyze_position` / `analyze` 對應的 Prompt 組裝流程：

```python
# 在組裝 human_prompt 時加入 history_section
history_section = build_position_history_section(state.get("prev_context"))

human_prompt = _POSITION_HUMAN_PROMPT.format(
    # ... 原有欄位 ...
    history_section=history_section,
)
```

在相關 human prompt 模板末尾加入佔位符：

```python
_POSITION_HUMAN_PROMPT = """...(原有內容)...

{history_section}
"""
```

**Step 4: 執行測試**

```bash
cd backend && pytest tests/test_langchain_analyzer.py -v
```

Expected: 全部 PASSED

**Step 5: 執行完整測試套件確認無回歸**

```bash
cd backend && pytest -v
```

Expected: 全部 PASSED

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py \
        backend/tests/test_langchain_analyzer.py
git commit -m "feat: add signal continuity section to position analysis prompt"
```

---

## Task 4: 勝率回測腳本

> **設計決策：CLI 腳本 vs. n8n Workflow C**
>
> Spec Section 4.4 描述的「優化回測流」定位為 n8n Workflow C（每週日自動執行）。
> Phase 8 先以 **CLI 腳本**形式實作，原因：
>
> 1. 需要 DB 累積至少 30 天有效數據後才有統計意義（Phase 7 完成後約 6 週）
> 2. 回測結果必須人工審核後才可調整權重，自動排程價值有限
> 3. CLI 版本可作為 n8n Workflow C 的 HTTP Endpoint 後端，屆時直接包裝成端點即可
>
> **Phase 8 完成後的後續動作**：數據足夠後，將 `backtest_win_rate.py` 封裝為
> `POST /internal/run-backtest` 端點，並在 n8n 建立 Workflow C（每週日 08:00）呼叫此端點，
> 結果透過 Telegram 發送週報（如 Phase 7 Task 7 規格）。

**Files:**

- Create: `backend/scripts/backtest_win_rate.py`
- Modify: `backend/requirements.txt`

**Step 1: 新增依賴**

在 `backend/requirements.txt` 加入：

```
scipy>=1.13.0
```

安裝：

```bash
cd backend && pip install scipy
```

**Step 2: 建立回測腳本**

```python
#!/usr/bin/env python
# backend/scripts/backtest_win_rate.py
"""
勝率回測腳本（Phase 8）

用法：
    python scripts/backtest_win_rate.py --days 90
    python scripts/backtest_win_rate.py --days 90 --action-tag Exit

定義：
    勝率 = Exit/Trim 訊號發出後 5 個交易日內，股價下跌 > 3% 的比率

輸出：
    各 action_tag 的勝率統計 + 各維度分數與預測結果的 Pearson 相關性
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

import yfinance as yf
from scipy.stats import pearsonr
from sqlalchemy import select

from ai_stock_sentinel.db.models import DailyAnalysisLog
from ai_stock_sentinel.db.session import AsyncSessionLocal


async def fetch_logs(days: int, action_tag: str | None) -> list[DailyAnalysisLog]:
    """從 DB 讀取指定期間的診斷 log。"""
    since = date.today() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        q = select(DailyAnalysisLog).where(DailyAnalysisLog.record_date >= since)
        if action_tag:
            q = q.where(DailyAnalysisLog.action_tag == action_tag)
        result = await db.execute(q)
        return list(result.scalars().all())


def fetch_price_5d_later(symbol: str, signal_date: date) -> float | None:
    """用 yfinance 取得訊號日後第 5 個交易日的收盤價。"""
    end = signal_date + timedelta(days=10)  # 多取幾天確保涵蓋 5 個交易日
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=signal_date.isoformat(), end=end.isoformat())
    if len(hist) < 5:
        return None
    return float(hist["Close"].iloc[4])


def fetch_signal_price(symbol: str, signal_date: date) -> float | None:
    """取得訊號日的收盤價。"""
    end = signal_date + timedelta(days=2)
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=signal_date.isoformat(), end=end.isoformat())
    if hist.empty:
        return None
    return float(hist["Close"].iloc[0])


def compute_win_rate(logs: list, threshold_pct: float = -3.0) -> dict:
    """計算 Exit/Trim 訊號的勝率（訊號後 5 日內下跌 > threshold_pct）。"""
    total = 0
    correct = 0
    skipped = 0

    for log in logs:
        p0 = fetch_signal_price(log.symbol, log.record_date)
        p5 = fetch_price_5d_later(log.symbol, log.record_date)

        if p0 is None or p5 is None:
            skipped += 1
            continue

        pct_change = (p5 - p0) / p0 * 100
        total += 1
        if pct_change <= threshold_pct:
            correct += 1

    return {
        "total":     total,
        "correct":   correct,
        "skipped":   skipped,
        "win_rate":  round(correct / total * 100, 1) if total > 0 else None,
    }


async def main(days: int, action_tag: str | None) -> None:
    print(f"\n=== 勝率回測報告（過去 {days} 天）===\n")

    logs = await fetch_logs(days, action_tag)
    if not logs:
        print("無符合條件的診斷紀錄。")
        return

    # 按 action_tag 分組統計勝率
    from collections import defaultdict
    by_tag: dict[str, list] = defaultdict(list)
    for log in logs:
        by_tag[log.action_tag].append(log)

    for tag, tag_logs in sorted(by_tag.items()):
        result = compute_win_rate(tag_logs)
        print(f"[{tag}]")
        print(f"  訊號次數：{result['total']}（跳過：{result['skipped']}）")
        print(f"  勝率（5日內下跌>3%）：{result['win_rate']}%\n")

    # Pearson 相關性分析（僅針對有 signal_confidence 的 Exit/Trim 訊號）
    exit_logs = [l for l in logs if l.action_tag in ("Exit", "Trim") and l.signal_confidence]
    if len(exit_logs) < 5:
        print("Exit/Trim 訊號筆數不足（< 5），跳過相關性分析。")
        return

    confidences = []
    outcomes = []
    for log in exit_logs:
        p0 = fetch_signal_price(log.symbol, log.record_date)
        p5 = fetch_price_5d_later(log.symbol, log.record_date)
        if p0 and p5:
            confidences.append(float(log.signal_confidence))
            outcomes.append(1 if (p5 - p0) / p0 * 100 <= -3.0 else 0)

    if len(confidences) >= 5:
        corr, pval = pearsonr(confidences, outcomes)
        print(f"=== 信心分數 vs 預測結果 Pearson 相關性 ===")
        print(f"  r = {corr:.3f}  (p = {pval:.3f})")
        if abs(corr) < 0.2:
            print("  ⚠️  相關性偏低，建議人工審核信心分數閾值設定。")
        else:
            print("  ✅  相關性合理，閾值設定有效。")

    print("\n注意：校準結果需人工審核後才可調整 confidence_scorer.py 的權重。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Stock Sentinel 勝率回測")
    parser.add_argument("--days",       type=int, default=90,   help="回測天數（預設 90）")
    parser.add_argument("--action-tag", type=str, default=None, help="篩選特定 action_tag")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.action_tag))
```

**Step 3: 確認腳本語法正確**

```bash
cd backend && python -m py_compile scripts/backtest_win_rate.py && echo "OK"
```

Expected: `OK`

**Step 4: 執行測試（需 Phase 7 DB 有數據，否則輸出「無符合條件」）**

```bash
cd backend && python scripts/backtest_win_rate.py --days 90
```

Expected: 正常輸出報告，或「無符合條件的診斷紀錄」（DB 尚無數據時正常）

**Step 5: Commit**

```bash
git add backend/scripts/backtest_win_rate.py backend/requirements.txt
git commit -m "feat: add win rate backtest script for Phase 8 model calibration"
```

---

## Task 5: 資料抓取併發優化（`asyncio.gather`）

**Files:**

- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`（或對應的 `crawl` node）
- Modify: `backend/tests/test_graph_nodes.py`

**背景**：目前 `crawl` 節點依序抓取技術面（yfinance）與籌碼面（institutional flow），兩者互相獨立，改為 `asyncio.gather` 並發執行可顯著縮短整體等待時間。

**Step 1: 寫失敗測試**

```python
# backend/tests/test_graph_nodes.py 新增
import asyncio
from unittest.mock import AsyncMock, patch

async def test_crawl_node_fetches_concurrently():
    """crawl node 應用 asyncio.gather 同時抓取技術面與籌碼面，不依序執行。"""
    call_order = []

    async def fake_fetch_technical(symbol):
        call_order.append("technical_start")
        await asyncio.sleep(0.05)
        call_order.append("technical_end")
        return {"close_price": 985.0}

    async def fake_fetch_institutional(symbol):
        call_order.append("institutional_start")
        await asyncio.sleep(0.05)
        call_order.append("institutional_end")
        return {"foreign_net": 12500}

    with patch("ai_stock_sentinel.graph.nodes.fetch_technical", fake_fetch_technical), \
         patch("ai_stock_sentinel.graph.nodes.fetch_institutional", fake_fetch_institutional):
        # 並發執行時，兩個 start 應該都在 end 之前出現
        from ai_stock_sentinel.graph.nodes import crawl_node
        await crawl_node({"symbol": "2330.TW"})

    # 並發：start 順序不固定，但兩個 start 應在任一 end 前出現
    first_end_idx = min(call_order.index("technical_end"), call_order.index("institutional_end"))
    assert call_order.index("technical_start") < first_end_idx
    assert call_order.index("institutional_start") < first_end_idx
```

**Step 2: 修改 crawl node 改用 asyncio.gather**

```python
# 修改前（依序抓取）
technical_data   = await fetch_technical(state["symbol"])
institutional_data = await fetch_institutional(state["symbol"])

# 修改後（並發抓取）
technical_data, institutional_data = await asyncio.gather(
    fetch_technical(state["symbol"]),
    fetch_institutional(state["symbol"]),
)
```

**Step 3: 執行測試**

```bash
cd backend && pytest tests/test_graph_nodes.py -v
```

Expected: 全部 PASSED

**Step 4: 執行完整測試套件確認無回歸**

```bash
cd backend && pytest -v
```

Expected: 全部 PASSED

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/graph/nodes.py \
        backend/tests/test_graph_nodes.py
git commit -m "perf: fetch technical and institutional data concurrently with asyncio.gather"
```

---

## 完成檢查清單

- [x] `GraphState` 有 `prev_context` 欄位，測試通過
- [x] `/analyze/position` 與 `/analyze` 呼叫前都會先 backfill 昨日未定稿快取，再讀取昨日上下文注入 state
- [x] `history_loader.py` 查詢來源為 `stock_analysis_cache`（非 `daily_analysis_log`），確保非持倉查詢也有昨日上下文
- [x] `build_position_history_section` 函式測試通過
- [x] 兩種分析流程的 LLM Prompt 都有【訊號連續性分析】區塊，數值來自 DB（非 LLM 推斷）
- [x] 完整測試套件無回歸（404 passed，1 pre-existing failure）
- [x] `backtest_win_rate.py` 語法正確，可正常執行
- [x] `fetch_external_data_node` 用 `asyncio.gather` 並發抓取籌碼面與基本面，測試通過
- [ ] （後續）數據足夠後封裝為 HTTP 端點，接入 n8n Workflow C 週報自動化

---

## 已知問題（2026-03-16 實作後發現）

### ✅ 問題 1：`ma5/ma20/ma60` 永遠存 `None`（`stock_analysis_cache.indicators`）

**已修正（2026-03-16）：**
- `GraphState` 新增 `ma5 / ma20 / ma60: float | None` 欄位（`_MarketDataStateFields` 與主類別）
- `strategy_node` 的 `updates` dict 補上 `"ma5": ma5, "ma20": ma20, "ma60": ma60`，確保存回 GraphState
- 修正後 `_extract_indicators` 可從 graph result 正確取得 MA 值，`prev_ma_alignment` 將反映真實均線方向

---

### ✅ 問題 2：`_estimate_cost` 未納入 `fundamental_context` 與 `history_section`

**已修正（2026-03-16）：**
- `_estimate_cost` 新增 `fundamental_context` 與 `history_section` 參數（預設 `None`），納入 combined 字串計算
- `analyze()` 呼叫 `_estimate_cost` 時傳入兩個新參數（`history_section` 由 `build_position_history_section(prev_context)` 產出）

---

_文件版本：v1.6 | 建立日期：2026-03-11 | 更新日期：2026-03-16 | 對應需求：`docs/ai-stock-sentinel-automation-review-spec.md` Phase 8（含 v1.7 backfill 規格）_
