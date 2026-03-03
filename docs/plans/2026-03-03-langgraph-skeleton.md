# LangGraph Skeleton Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立 LangGraph 狀態機骨架，包含 graph state、stub 版 judge 節點、retry/loop guard，並補對應測試，讓 Phase 2 回圈有可跑的結構。

**Architecture:** 新增 `backend/src/ai_stock_sentinel/graph/` 模組，定義 `GraphState`（TypedDict）與三個節點函式（`crawl`、`judge_data_sufficiency`、`analyze`）。Judge 節點此階段為 stub（永遠回傳 sufficient），但結構完整，後續可填入真正邏輯。Graph 透過 `langgraph.graph.StateGraph` 組裝，由 API 的 `/analyze` 選擇性呼叫（現有 `StockCrawlerAgent` 路徑維持不動）。

**Tech Stack:** Python 3.11+, langgraph, langchain, pytest, FastAPI（已裝）

---

## 背景知識

### 現有檔案結構
```
backend/
  src/ai_stock_sentinel/
    agents/crawler_agent.py   ← 現有線性流程
    api.py                    ← FastAPI，依賴 StockCrawlerAgent
    main.py                   ← build_agent(), CLI 入口
    models.py                 ← StockSnapshot dataclass
    analysis/
      interface.py
      langchain_analyzer.py
      news_cleaner.py
    data_sources/
      yfinance_client.py
  tests/
    test_api.py
  requirements.txt
  Makefile                    ← make test = PYTHONPATH=src ./venv/bin/python -m pytest -q
```

### 執行測試
```bash
cd backend
make test
```

### LangGraph 基本概念
- `StateGraph` 接受一個 TypedDict 作為 state schema
- 用 `.add_node(name, fn)` 加入節點，fn 簽名為 `(state: GraphState) -> dict`（回傳要更新的 key）
- 用 `.add_edge` / `.add_conditional_edges` 連接節點
- 用 `.set_entry_point` 設定起點，`.set_finish_point` 或特殊 `END` 節點設定終點
- `.compile()` 產出可呼叫的 graph，用 `graph.invoke(initial_state)` 執行

---

## Task 1：安裝 langgraph

**Files:**
- Modify: `backend/requirements.txt`

**Step 1: 在 requirements.txt 末尾加一行**

```
langgraph>=0.2.0
```

**Step 2: 安裝**

```bash
cd backend
make install
```

Expected: 看到 `Successfully installed langgraph-...`（或已安裝訊息）

**Step 3: 確認可 import**

```bash
cd backend
./venv/bin/python -c "import langgraph; print(langgraph.__version__)"
```

Expected: 印出版本號，無 ImportError

**Step 4: Commit**

```bash
cd backend
git add requirements.txt
git commit -m "chore: add langgraph dependency"
```

---

## Task 2：建立 GraphState

**Files:**
- Create: `backend/src/ai_stock_sentinel/graph/__init__.py`
- Create: `backend/src/ai_stock_sentinel/graph/state.py`
- Create: `backend/tests/test_graph_state.py`

### Step 1: 寫失敗測試

建立 `backend/tests/test_graph_state.py`：

```python
from __future__ import annotations

from ai_stock_sentinel.graph.state import GraphState


def test_graph_state_fields_exist() -> None:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
    }
    assert state["symbol"] == "2330.TW"
    assert state["retry_count"] == 0
    assert state["data_sufficient"] is False
```

### Step 2: 執行確認失敗

```bash
cd backend
make test
```

Expected: FAIL，`ModuleNotFoundError: No module named 'ai_stock_sentinel.graph'`

### Step 3: 建立模組

建立空的 `backend/src/ai_stock_sentinel/graph/__init__.py`（空檔）。

建立 `backend/src/ai_stock_sentinel/graph/state.py`：

```python
from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict):
    symbol: str
    news_content: str | None
    snapshot: dict[str, Any] | None
    analysis: str | None
    cleaned_news: dict[str, Any] | None
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
```

### Step 4: 執行確認通過

```bash
cd backend
make test
```

Expected: 所有測試 PASS（包含既有 API 測試）

### Step 5: Commit

```bash
cd backend
git add src/ai_stock_sentinel/graph/__init__.py \
        src/ai_stock_sentinel/graph/state.py \
        tests/test_graph_state.py
git commit -m "feat: add GraphState TypedDict for LangGraph"
```

---

## Task 3：建立節點函式（stub）

**Files:**
- Create: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Create: `backend/tests/test_graph_nodes.py`

### Step 1: 寫失敗測試

建立 `backend/tests/test_graph_nodes.py`：

```python
from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock

from ai_stock_sentinel.graph.nodes import crawl_node, judge_node, analyze_node
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import StockSnapshot


def _make_snapshot() -> dict:
    return asdict(StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    ))


def _base_state(**overrides) -> GraphState:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
    }
    state.update(overrides)
    return state


def test_crawl_node_returns_snapshot(monkeypatch) -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    )

    result = crawl_node(_base_state(), crawler=mock_crawler)

    assert result["snapshot"]["symbol"] == "2330.TW"
    assert result["errors"] == []


def test_judge_node_stub_always_sufficient() -> None:
    state = _base_state(snapshot=_make_snapshot())

    result = judge_node(state)

    assert result["data_sufficient"] is True


def test_analyze_node_returns_analysis_string(monkeypatch) -> None:
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = "分析結果"

    state = _base_state(snapshot=_make_snapshot())
    result = analyze_node(state, analyzer=mock_analyzer)

    assert result["analysis"] == "分析結果"
```

### Step 2: 執行確認失敗

```bash
cd backend
make test
```

Expected: FAIL，`cannot import name 'crawl_node'`

### Step 3: 實作節點

建立 `backend/src/ai_stock_sentinel/graph/nodes.py`：

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.state import GraphState


def crawl_node(state: GraphState, *, crawler: YFinanceCrawler) -> dict[str, Any]:
    """抓取股票快照，回傳更新的 state keys。"""
    try:
        snapshot = crawler.fetch_basic_snapshot(symbol=state["symbol"])
        return {"snapshot": asdict(snapshot), "errors": []}
    except Exception as exc:
        return {
            "snapshot": None,
            "errors": state["errors"] + [{"code": "CRAWL_ERROR", "message": str(exc)}],
        }


def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷資料是否充分。此為 stub：永遠回傳 sufficient=True。"""
    return {"data_sufficient": True}


def analyze_node(state: GraphState, *, analyzer: StockAnalyzer) -> dict[str, Any]:
    """執行分析，回傳 analysis 字串。"""
    from ai_stock_sentinel.models import StockSnapshot

    snapshot_dict = state["snapshot"] or {}
    snapshot = StockSnapshot(**snapshot_dict)
    analysis = analyzer.analyze(snapshot)
    return {"analysis": analysis}
```

### Step 4: 執行確認通過

```bash
cd backend
make test
```

Expected: 所有測試 PASS

### Step 5: Commit

```bash
cd backend
git add src/ai_stock_sentinel/graph/nodes.py \
        tests/test_graph_nodes.py
git commit -m "feat: add LangGraph stub nodes (crawl, judge, analyze)"
```

---

## Task 4：組裝 Graph + loop guard

**Files:**
- Create: `backend/src/ai_stock_sentinel/graph/builder.py`
- Create: `backend/tests/test_graph_builder.py`

### 背景：LangGraph conditional edges

```python
graph.add_conditional_edges(
    "judge",
    routing_fn,           # 接受 state，回傳下一個節點名稱（字串）
    {"analyze": "analyze", "crawl": "crawl", END: END},
)
```

`END` 從 `langgraph.graph` import。

### Step 1: 寫失敗測試

建立 `backend/tests/test_graph_builder.py`：

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.models import StockSnapshot
from dataclasses import asdict


def _make_snapshot() -> dict:
    return asdict(StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    ))


def test_graph_runs_and_returns_analysis() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    )
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = "分析結果"

    graph = build_graph(crawler=mock_crawler, analyzer=mock_analyzer)
    result = graph.invoke({
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
    })

    assert result["analysis"] == "分析結果"
    assert result["errors"] == []


def test_graph_loop_guard_stops_after_max_retries() -> None:
    """Judge 永遠說 insufficient 時，graph 應在 max_retries 後停止。"""
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    )
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = "分析"

    # 用 always_insufficient=True 觸發回圈上限
    graph = build_graph(
        crawler=mock_crawler,
        analyzer=mock_analyzer,
        max_retries=2,
        _force_insufficient=True,
    )
    result = graph.invoke({
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
    })

    assert result["retry_count"] >= 2
```

### Step 2: 執行確認失敗

```bash
cd backend
make test
```

Expected: FAIL，`cannot import name 'build_graph'`

### Step 3: 實作 builder

建立 `backend/src/ai_stock_sentinel/graph/builder.py`：

```python
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.nodes import analyze_node, crawl_node, judge_node
from ai_stock_sentinel.graph.state import GraphState

MAX_RETRIES = 3


def build_graph(
    *,
    crawler: YFinanceCrawler,
    analyzer: StockAnalyzer,
    max_retries: int = MAX_RETRIES,
    _force_insufficient: bool = False,
):
    """組裝並編譯 LangGraph 狀態機。

    _force_insufficient: 測試用，強制讓 judge 永遠回傳 insufficient。
    """
    graph = StateGraph(GraphState)

    # 節點
    graph.add_node("crawl", partial(crawl_node, crawler=crawler))
    graph.add_node("analyze", partial(analyze_node, analyzer=analyzer))

    def _judge(state: GraphState) -> dict[str, Any]:
        if _force_insufficient:
            return {"data_sufficient": False}
        return judge_node(state)

    graph.add_node("judge", _judge)

    # 邊
    graph.set_entry_point("crawl")
    graph.add_edge("crawl", "judge")

    def _route(state: GraphState) -> str:
        if state["data_sufficient"]:
            return "analyze"
        if state["retry_count"] >= max_retries:
            return "analyze"  # 超過上限，強制往下走
        return "crawl"

    def _increment_retry(state: GraphState) -> dict[str, Any]:
        """在回到 crawl 前先增加 retry_count。"""
        return {"retry_count": state["retry_count"] + 1}

    graph.add_node("increment_retry", _increment_retry)

    graph.add_conditional_edges(
        "judge",
        _route,
        {
            "analyze": "analyze",
            "crawl": "increment_retry",
        },
    )
    graph.add_edge("increment_retry", "crawl")
    graph.add_edge("analyze", END)

    return graph.compile()
```

### Step 4: 執行確認通過

```bash
cd backend
make test
```

Expected: 所有測試 PASS

### Step 5: Commit

```bash
cd backend
git add src/ai_stock_sentinel/graph/builder.py \
        tests/test_graph_builder.py
git commit -m "feat: build LangGraph graph with loop guard (max_retries)"
```

---

## Task 5: 更新文件

**Files:**
- Modify: `docs/progress-tracker.md`
- Modify: `docs/implementation-task-breakdown.md`

### Step 1: 更新 progress-tracker.md

在「進行中 / 待完成」的 Phase 2 區塊，把以下項目改為完成：

```markdown
### Phase 2：LangGraph
- [x] 建立 LangGraph 狀態機（GraphState + 節點 stub + builder）
- [x] loop guard（max_retries）骨架
- [ ] 完整性判斷節點（judge 邏輯填入）
- [ ] 新聞 RSS 自動抓取
```

並更新高層完成度：
```
- **Phase 2（LangGraph 回圈）**：約 30%（骨架可跑，judge 為 stub）
```

### Step 2: 更新 implementation-task-breakdown.md

在 P2-1 下方加一行 DoD 驗證記錄：

```markdown
- **完成記錄**：GraphState + stub 節點 + loop guard 已實作，測試覆蓋，2026-03-03
```

### Step 3: Commit

```bash
git add docs/progress-tracker.md docs/implementation-task-breakdown.md
git commit -m "docs: update progress for LangGraph skeleton (P2-1 partial)"
```

---

## 驗收標準（完成後確認）

```bash
cd backend
make test
```

全部 PASS，且包含：
- `test_graph_state.py` — GraphState 欄位
- `test_graph_nodes.py` — crawl/judge/analyze stub
- `test_graph_builder.py` — graph 可執行 + loop guard

```bash
cd backend
./venv/bin/python -c "
from ai_stock_sentinel.graph.builder import build_graph
print('build_graph import OK')
"
```

Expected: `build_graph import OK`
