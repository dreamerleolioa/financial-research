# Architecture Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修復四個架構層級的問題：`requires_fundamental_update` routing 失效、N+1 frontend history fetch、外部資料在 retry loop 內重複抓取、RSI 重複計算。

**Architecture:** 依影響範圍分為四個獨立 task。Task 1 調整 graph routing 邏輯，Task 2 新增後端 bulk history endpoint + 前端改用它，Task 3 重新設計 graph 拓撲將外部資料抓取移到 retry loop 外，Task 4 消除 RSI 重複計算。每個 task 可獨立測試與 commit。

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, SQLAlchemy, React 18, TypeScript

---

## Task 1：修復 `requires_fundamental_update` routing — graph 忽略 fundamental update flag

### 背景

`_route()` 函式（`builder.py:78-85`）只檢查 `requires_news_refresh`，從未檢查 `requires_fundamental_update`。`judge_node` 在 snapshot 為 None 時設定 `requires_fundamental_update=True`，但 router 看不到它，導致此 flag 無法觸發任何 re-fetch 行為。

目前 `_route` 邏輯：
1. `data_sufficient` → 往下走
2. `retry_count >= max_retries` → 強制往下走
3. `requires_news_refresh` → 去 `fetch_news`
4. 其他 → 去 `increment_retry → crawl`（重新 crawl）

問題：`requires_fundamental_update` 落在情況 4，會觸發 crawl retry，但 crawl 只抓 snapshot，不抓 institutional/fundamental 資料。`fetch_external_data` 在 crawl 後面，所以 retry 路徑上其實也會重新跑 `fetch_external_data`。

真正的問題是：**`requires_fundamental_update` flag 沒有獨立語義**，目前行為和不設這個 flag 完全一樣（都走 crawl retry）。這個 flag 的正確意圖應該是：只更新 institutional/fundamental 資料，不重新 crawl snapshot。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/builder.py:78-95`
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py` — 新增 `fetch_external_data_only_node`（只重跑 external data，skip crawl）
- Test: `backend/tests/test_graph_builder.py`

### Step 1: 在 `nodes.py` 確認 `fetch_external_data_node` 可以被單獨呼叫

讀 `nodes.py` 確認 `fetch_external_data_node` 函式簽名不依賴 crawl 的輸出（只需 `state["symbol"]` 和 `state["snapshot"]`）。若 snapshot 已存在，直接重跑 `fetch_external_data_node` 即可。

### Step 2: 修改 `_route` 加入 `requires_fundamental_update` 分支

找到 `builder.py:78-85` 的 `_route` 函式，改為：

```python
def _route(state: GraphState) -> str:
    if state["data_sufficient"]:
        return "clean"
    if state["retry_count"] >= max_retries:
        return "clean"  # 超過上限，強制往下走
    if state.get("requires_fundamental_update") and state.get("snapshot") is not None:
        return "fetch_external_data"  # snapshot 已有，只重抓 external data
    if state["requires_news_refresh"]:
        return "fetch_news"
    return "increment_retry"
```

同時在 `add_conditional_edges` 的 mapping 加入新路由：

```python
graph.add_conditional_edges(
    "judge",
    _route,
    {
        "clean":               "clean",
        "fetch_news":          "fetch_news",
        "fetch_external_data": "fetch_external_data",
        "increment_retry":     "increment_retry",
    },
)
```

### Step 3: 寫測試

在 `backend/tests/test_graph_builder.py` 新增：

```python
def test_graph_routes_to_fetch_external_data_when_fundamental_update_needed():
    """requires_fundamental_update=True 且 snapshot 已有時，應跳過 crawl 直接重跑 fetch_external_data。"""
    mock_crawler = MagicMock()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="ok")

    call_log = []

    def counting_institutional(symbol):
        call_log.append("institutional")
        return {}

    def counting_fundamental(symbol, price):
        call_log.append("fundamental")
        return {}

    graph = build_graph(
        crawler=mock_crawler,
        analyzer=mock_analyzer,
        institutional_fetcher=counting_institutional,
        fundamental_fetcher=counting_fundamental,
    )

    # snapshot 已有，但 requires_fundamental_update=True
    state = _initial_state()
    state["snapshot"] = {
        "symbol": "2330.TW", "current_price": 100.0,
        "recent_closes": [99.0, 100.0], "volume": 1000,
    }
    state["requires_fundamental_update"] = True
    state["is_final"] = True

    result = graph.invoke(state)
    # crawl_node 不應被呼叫（snapshot 已有走 fetch_external_data 路徑）
    mock_crawler.fetch_basic_snapshot.assert_not_called()
    # fetch_external_data 應被呼叫一次
    assert call_log.count("institutional") >= 1
```

### Step 4: 執行測試確認通過

```bash
cd backend
uv run pytest tests/test_graph_builder.py -v -k "fundamental_update"
```

Expected: PASS

### Step 5: 執行完整測試

```bash
uv run pytest tests/ -k "not slow" -v 2>&1 | tail -15
```

Expected: 全部 PASS

### Step 6: Commit

```bash
git add backend/src/ai_stock_sentinel/graph/builder.py
git commit -m "fix: route to fetch_external_data when requires_fundamental_update and snapshot exists"
```

---

## Task 2：修復 N+1 fetch — Portfolio 頁面改用 bulk history endpoint

### 背景

`PortfolioPage.tsx:492-504` 載入 portfolio 列表後，用 `Promise.all(data.map(...))` 對每個 item 各發一次 `/portfolio/{id}/history?limit=1`。若用戶有 5 個持倉就發 5 個請求。

解法：後端新增 `GET /portfolio/latest-history` bulk endpoint，一次回傳所有持倉的最新一筆 log；前端改用此 endpoint 取代 N 個並行請求。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/portfolio/history_router.py` — 新增 bulk endpoint
- Modify: `frontend/src/pages/PortfolioPage.tsx:492-510` — 改用 bulk endpoint
- Test: `backend/tests/test_portfolio_router.py`

### Step 1: 了解現有 DB 結構

`DailyAnalysisLog` (`db/models.py:36-55`) 有以下欄位：
- `user_id`, `symbol`, `record_date`, `signal_confidence`, `action_tag`, `recommended_action`, `indicators`, `final_verdict`, `prev_action_tag`, `prev_confidence`, `analysis_is_final`

`UserPortfolio` (`db/models.py:18-33`) 有 `id`, `user_id`, `symbol`, `is_active`。

### Step 2: 在 `history_router.py` 新增 bulk endpoint

在 `history_router.py` 現有的 `get_portfolio_history` endpoint 之後新增：

```python
from sqlalchemy import and_, case

@router.get("/latest-history")
def get_portfolio_latest_history(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """回傳當前用戶所有 active 持倉的最新一筆 DailyAnalysisLog（bulk，單一 query）。"""
    # 取得用戶所有 active 持倉的 symbol
    portfolios = db.execute(
        select(UserPortfolio.id, UserPortfolio.symbol).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        )
    ).all()

    if not portfolios:
        return {}

    symbols = [p.symbol for p in portfolios]

    # 用 DISTINCT ON (symbol) + ORDER BY record_date DESC 取每個 symbol 最新一筆
    # 等效於每個 symbol 的 MAX(record_date) join
    subq = (
        select(
            DailyAnalysisLog,
            func.row_number()
            .over(
                partition_by=DailyAnalysisLog.symbol,
                order_by=DailyAnalysisLog.record_date.desc(),
            )
            .label("rn"),
        )
        .where(
            DailyAnalysisLog.user_id == current_user.id,
            DailyAnalysisLog.symbol.in_(symbols),
        )
        .subquery()
    )

    rows = db.execute(
        select(subq).where(subq.c.rn == 1)
    ).mappings().all()

    # 以 symbol 為 key 建立 map
    latest_by_symbol: dict[str, dict] = {}
    for row in rows:
        latest_by_symbol[row["symbol"]] = {
            "record_date":        row["record_date"].isoformat(),
            "signal_confidence":  float(row["signal_confidence"]) if row["signal_confidence"] else None,
            "action_tag":         row["action_tag"],
            "recommended_action": row["recommended_action"],
            "indicators":         row["indicators"],
            "final_verdict":      row["final_verdict"],
            "prev_action_tag":    row["prev_action_tag"],
            "prev_confidence":    float(row["prev_confidence"]) if row["prev_confidence"] else None,
        }

    # 以 portfolio_id 為 key 回傳
    return {
        p.id: latest_by_symbol.get(p.symbol)
        for p in portfolios
    }
```

注意：需要在檔案頂部確認 `func`, `select` 已 import（現有 import 已包含）。需補 `from sqlalchemy.orm import aliased` 若使用。

### Step 3: 寫測試

在 `backend/tests/test_portfolio_router.py` 新增：

```python
def test_latest_history_returns_empty_when_no_portfolios(auth_client):
    resp = auth_client.get("/portfolio/latest-history")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_latest_history_returns_latest_per_portfolio(auth_client, db_session, test_user):
    """有多筆 log 時應只回傳最新一筆，且 key 為 portfolio_id（int）。"""
    from datetime import date
    from ai_stock_sentinel.db.models import DailyAnalysisLog, UserPortfolio

    # 建立持倉
    portfolio = UserPortfolio(
        user_id=test_user.id, symbol="2330.TW",
        entry_price=100.0, entry_date=date(2026, 1, 1), quantity=1,
    )
    db_session.add(portfolio)
    db_session.flush()

    # 舊 log
    db_session.add(DailyAnalysisLog(
        user_id=test_user.id, symbol="2330.TW",
        record_date=date(2026, 3, 1), action_tag="Hold",
    ))
    # 新 log
    db_session.add(DailyAnalysisLog(
        user_id=test_user.id, symbol="2330.TW",
        record_date=date(2026, 3, 10), action_tag="Trim",
    ))
    db_session.commit()

    resp = auth_client.get("/portfolio/latest-history")
    assert resp.status_code == 200
    data = resp.json()
    key = str(portfolio.id)
    assert key in data
    assert data[key]["action_tag"] == "Trim"
    assert data[key]["record_date"] == "2026-03-10"
```

### Step 4: 執行測試

```bash
cd backend
uv run pytest tests/test_portfolio_router.py -v -k "latest_history"
```

Expected: PASS（若測試 fixture 不存在，查看同檔案現有 fixture 模式並照做）

### Step 5: 更新前端 `PortfolioPage.tsx`

找到 `PortfolioPage.tsx` 的 `loadPortfolio` 函式（約 line 483），將：

```typescript
const entries = await Promise.all(
  data.map(async (item) => {
    try {
      const r = await fetch(
        `${import.meta.env.VITE_API_URL}/portfolio/${item.id}/history?limit=1`,
        { headers: authHeaders() },
      );
      if (!r.ok) return [item.id, null] as const;
      const body: { records: HistoryEntry[] } = await r.json();
      return [item.id, body.records[0] ?? null] as const;
    } catch {
      return [item.id, null] as const;
    }
  }),
);
setLatestMap(Object.fromEntries(entries));
```

改為：

```typescript
try {
  const r = await fetch(
    `${import.meta.env.VITE_API_URL}/portfolio/latest-history`,
    { headers: authHeaders() },
  );
  if (r.ok) {
    const latestData: Record<number, HistoryEntry | null> = await r.json();
    setLatestMap(latestData);
  }
} catch { /* ignore */ }
```

### Step 6: TypeScript 型別確認

```bash
cd frontend
npx tsc --noEmit
```

Expected: 無錯誤

### Step 7: 執行後端完整測試

```bash
cd backend
uv run pytest tests/ -k "not slow" 2>&1 | tail -5
```

Expected: 全部 PASS

### Step 8: Commit

```bash
git add backend/src/ai_stock_sentinel/portfolio/history_router.py \
        frontend/src/pages/PortfolioPage.tsx
git commit -m "feat: add bulk latest-history endpoint and remove N+1 frontend fetch"
```

---

## Task 3：外部資料在 retry loop 內重複抓取 — 將 `fetch_external_data` 移到 retry loop 外

### 背景

現有 graph 拓撲（`builder.py`）：

```
crawl → fetch_external_data → judge → [retry: increment_retry → crawl → fetch_external_data → judge → ...]
```

`fetch_external_data` 在 retry loop 內，每次 retry 都重新抓 institutional flow 和 fundamental data（yfinance、TWSE API）。這是不必要的：這些資料在 retry 間不會改變，且是高延遲的外部呼叫。

目標拓撲：

```
crawl → judge_snapshot → fetch_external_data → fetch_news_loop → judge_full → clean → ...
```

或更簡單的做法：在 `judge_node` 前先判斷 snapshot 是否已存在，若已存在就跳過 `fetch_external_data`。

**最小改動方案：** 在 `fetch_external_data_node` 內部加入 skip guard — 若 `institutional_flow` 和 `fundamental_data` 都已存在於 state，直接 return 空 dict（不重新抓）。這樣拓撲不需要改動。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py` — `fetch_external_data_node` 加 skip guard
- Test: `backend/tests/test_graph_nodes.py`
- Test: `backend/tests/test_graph_builder.py`

### Step 1: 讀 `fetch_external_data_node` 現有實作

找到 `nodes.py` 中 `fetch_external_data_node` 函式（約 line 52-81），確認其 return 格式。

### Step 2: 加入 skip guard

在函式最頂部（symbol/snapshot 取得後，asyncio.run 之前）加入：

```python
def fetch_external_data_node(
    state: GraphState,
    *,
    institutional_fetcher: Callable[[str], dict[str, Any]],
    fundamental_fetcher: Callable[[str, float], dict[str, Any]],
) -> dict[str, Any]:
    # Skip guard：若 external data 已存在（前一輪 retry 已抓過），不重複呼叫外部 API
    if state.get("institutional_flow") is not None and state.get("fundamental_data") is not None:
        return {}

    symbol = state["symbol"]
    # ... 其餘不變
```

### Step 3: 寫測試確認 skip guard 生效

在 `backend/tests/test_graph_nodes.py` 新增：

```python
def test_fetch_external_data_node_skips_when_data_already_present():
    """institutional_flow 和 fundamental_data 都已存在時，不應再呼叫外部 fetcher。"""
    inst_calls = []
    fund_calls = []

    def mock_inst(symbol):
        inst_calls.append(symbol)
        return {"flow": "new"}

    def mock_fund(symbol, price):
        fund_calls.append(symbol)
        return {"pe": 99}

    state = _base_state(
        snapshot={"current_price": 100.0, "recent_closes": []},
        institutional_flow={"flow": "existing"},
        fundamental_data={"pe": 20},
    )

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node
    result = fetch_external_data_node(
        state,
        institutional_fetcher=mock_inst,
        fundamental_fetcher=mock_fund,
    )

    assert inst_calls == [], "institutional fetcher should not be called"
    assert fund_calls == [], "fundamental fetcher should not be called"
    assert result == {}
```

### Step 4: 執行測試

```bash
cd backend
uv run pytest tests/test_graph_nodes.py -v -k "skip_when_data_already"
```

Expected: PASS

### Step 5: 寫整合測試確認 retry 時 external data 只抓一次

在 `backend/tests/test_graph_builder.py` 新增：

```python
def test_fetch_external_data_called_once_across_retries():
    """即使觸發 retry，external data fetcher 只應被呼叫一次。"""
    mock_crawler = MagicMock()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = AnalysisDetail(summary="ok")

    # 第一次 crawl 回傳 snapshot，但第一次 judge 觸發 news refresh
    snapshot = _make_stock_snapshot()
    mock_crawler.fetch_basic_snapshot.return_value = snapshot

    inst_calls = []
    def counting_inst(symbol):
        inst_calls.append(symbol)
        return {}

    mock_rss = MagicMock()
    # 第一次 fetch_news 後讓 judge 通過
    mock_rss.fetch_latest_news.return_value = []

    graph = build_graph(
        crawler=mock_crawler,
        analyzer=mock_analyzer,
        institutional_fetcher=counting_inst,
        fundamental_fetcher=lambda s, p: {},
        rss_client=mock_rss,
    )

    state = _initial_state()
    state["is_final"] = True
    graph.invoke(state)

    # 不論 retry 幾次，institutional fetcher 只呼叫一次
    assert len(inst_calls) == 1, f"Expected 1 call, got {len(inst_calls)}"
```

### Step 6: 執行完整測試

```bash
cd backend
uv run pytest tests/ -k "not slow" 2>&1 | tail -5
```

Expected: 全部 PASS

### Step 7: Commit

```bash
git add backend/src/ai_stock_sentinel/graph/nodes.py
git commit -m "perf: skip fetch_external_data when data already present in state (avoid re-fetch on retry)"
```

---

## Task 4：消除 RSI 重複計算

### 背景

`calc_rsi(closes, period=14)` 被呼叫三次：
- `preprocess_node` (`nodes.py:188`) — 計算並存入 `state["rsi14"]`
- `score_node` (`nodes.py:240`) — 重新計算，不讀 state
- `strategy_node` (`nodes.py:509`) — 重新計算，不讀 state

`score_node` 和 `strategy_node` 應直接讀 `state["rsi14"]`（已由 preprocess 計算），不需重算。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py` — `score_node` 和 `strategy_node` 改讀 state
- Test: `backend/tests/test_graph_nodes.py`

### Step 1: 確認 `preprocess_node` 的 `rsi14` 存入 state

確認 `nodes.py:190-200` 的 `updates` dict 包含 `"rsi14": rsi14_val`，且 `state["rsi14"]` 在 `score_node` / `strategy_node` 執行時已存在。

### Step 2: 修改 `score_node` 讀 state rsi14

找到 `score_node` 中 `rsi = calc_rsi(closes, period=14)` 的那行（約 line 240），改為：

```python
rsi: float | None = state.get("rsi14")
```

同時確認後面的 `closes` 變數不再被其他地方的 RSI 使用（只用於 `calc_bias` 和 `derive_technical_score`，那些保持不變）。

### Step 3: 修改 `strategy_node` 讀 state rsi14

找到 `strategy_node` 中 `rsi: float | None = calc_rsi(closes, period=14) if closes else None`（約 line 509），改為：

```python
rsi: float | None = state.get("rsi14")
```

### Step 4: 確認 `calc_rsi` import 是否還有其他使用者

```bash
grep -n "calc_rsi" backend/src/ai_stock_sentinel/graph/nodes.py
```

若只剩 `preprocess_node` 使用，其他地方的移除不影響 import（保留 import 即可）。

### Step 5: 執行測試

```bash
cd backend
uv run pytest tests/ -k "not slow" -v 2>&1 | tail -15
```

Expected: 全部 PASS（行為不變，只是少算兩次）

### Step 6: Commit

```bash
git add backend/src/ai_stock_sentinel/graph/nodes.py
git commit -m "perf: read rsi14 from state in score_node and strategy_node instead of recomputing"
```

---

## 執行順序總覽

| Task | 分類 | 影響範圍 | 難度 |
|------|------|----------|------|
| 1 | Bug fix | graph routing | 中 |
| 2 | Perf | backend + frontend | 中 |
| 3 | Perf | graph node | 低 |
| 4 | Perf | graph nodes | 低 |

建議順序：Task 4 → Task 3 → Task 2 → Task 1（由簡至繁）。
