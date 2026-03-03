# Judge Node Logic Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 將 `judge_node` 從 stub 升級為真正的資料完整性判斷，根據 snapshot 欄位、新聞新鮮度與數字密度決定是否需要重新抓取。

**Architecture:** 在 `nodes.py` 的 `judge_node` 填入判斷規則（純函式，不依賴外部 IO）。同時在 `GraphState` 加入 `requires_news_refresh` 與 `requires_fundamental_update` flags，以便後續節點知道缺失原因。判斷邏輯抽到獨立的 `_check_sufficiency` 函式，方便測試。

**Tech Stack:** Python 3.11+, pytest（無新依賴）

---

## 背景知識

### 現有檔案

```
backend/src/ai_stock_sentinel/graph/
  state.py    ← GraphState TypedDict
  nodes.py    ← crawl_node, judge_node (stub), analyze_node
  builder.py  ← build_graph()
```

### 目前 judge_node（stub）

```python
def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷資料是否充分。此為 stub：永遠回傳 sufficient=True。"""
    return {"data_sufficient": True}
```

### 判斷規則（三條）

1. **snapshot 缺失**：`state["snapshot"]` 為 None → insufficient，`requires_fundamental_update=True`
2. **新聞過舊**：`cleaned_news.date` 存在，但距今超過 7 天 → insufficient，`requires_news_refresh=True`
3. **數字不足**：`cleaned_news.mentioned_numbers` 存在，但為空 list → insufficient，`requires_news_refresh=True`

任一條件成立 → `data_sufficient=False`
三條全部通過 → `data_sufficient=True`

### 執行測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

---

## Task 1：擴充 GraphState，加入 reason flags

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/state.py`
- Modify: `backend/tests/test_graph_state.py`

### Step 1: 寫失敗測試

在 `backend/tests/test_graph_state.py` 末尾加入新測試：

```python
def test_graph_state_includes_reason_flags() -> None:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
    }
    assert state["requires_news_refresh"] is False
    assert state["requires_fundamental_update"] is False
```

### Step 2: 執行確認失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

Expected: FAIL，`test_graph_state_includes_reason_flags` 因 GraphState 缺少新欄位而失敗（TypedDict 多餘 key 在 runtime 不報錯，但 test 確認欄位存在）

> 注意：TypedDict 在 runtime 是 plain dict，不會因多餘 key 報錯。測試的目的是確認可以正確建構並存取新欄位，且 state.py 已加入定義（方便 type checker）。先讓測試跑通，但也要確認 state.py 有更新。

### Step 3: 更新 state.py

在 `GraphState` 末尾加入兩個新欄位：

```python
class GraphState(TypedDict):
    symbol: str
    news_content: str | None
    snapshot: dict[str, Any] | None
    analysis: str | None
    cleaned_news: dict[str, Any] | None
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
    requires_news_refresh: bool
    requires_fundamental_update: bool
```

### Step 4: 執行確認通過

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

Expected: 所有測試 PASS（包含既有測試）

### Step 5: Commit

```bash
cd /Users/leo/Documents/work/financial-research/backend
git add src/ai_stock_sentinel/graph/state.py tests/test_graph_state.py
git commit -m "feat: add reason flags to GraphState"
```

---

## Task 2：實作 judge_node 判斷邏輯

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Modify: `backend/tests/test_graph_nodes.py`

### Step 1: 寫失敗測試

在 `backend/tests/test_graph_nodes.py` 末尾加入新測試：

```python
from datetime import date, timedelta


def _make_cleaned_news(
    *,
    date_str: str | None = None,
    mentioned_numbers: list[str] | None = None,
) -> dict:
    today = date.today().isoformat()
    return {
        "date": date_str if date_str is not None else today,
        "title": "台積電 2 月營收年增",
        "mentioned_numbers": mentioned_numbers if mentioned_numbers is not None else ["2,600", "18.2%"],
        "sentiment_label": "positive",
    }


def test_judge_node_sufficient_when_snapshot_and_fresh_news() -> None:
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is True
    assert result["requires_news_refresh"] is False
    assert result["requires_fundamental_update"] is False


def test_judge_node_insufficient_when_snapshot_missing() -> None:
    state = _base_state(snapshot=None)
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_fundamental_update"] is True


def test_judge_node_insufficient_when_news_stale() -> None:
    stale_date = (date.today() - timedelta(days=8)).isoformat()
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(date_str=stale_date),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_insufficient_when_no_mentioned_numbers() -> None:
    state = _base_state(
        snapshot=_make_snapshot(),
        cleaned_news=_make_cleaned_news(mentioned_numbers=[]),
    )
    result = judge_node(state)
    assert result["data_sufficient"] is False
    assert result["requires_news_refresh"] is True


def test_judge_node_sufficient_when_no_news_provided() -> None:
    """cleaned_news 為 None 時（未提供新聞），不因此判定為 insufficient。"""
    state = _base_state(snapshot=_make_snapshot(), cleaned_news=None)
    result = judge_node(state)
    assert result["data_sufficient"] is True
```

### Step 2: 執行確認失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

Expected: 多個 FAIL（stub judge_node 不符合新測試的斷言）

### Step 3: 實作 judge_node

把 `nodes.py` 的 `judge_node` 替換為：

```python
from datetime import date


NEWS_STALENESS_DAYS = 7


def _check_sufficiency(state: GraphState) -> tuple[bool, bool, bool]:
    """
    回傳 (data_sufficient, requires_news_refresh, requires_fundamental_update)。
    """
    requires_news_refresh = False
    requires_fundamental_update = False

    # 規則 1：snapshot 缺失
    if not state["snapshot"]:
        requires_fundamental_update = True

    # 規則 2 & 3：新聞相關（只在有提供新聞時才判斷）
    cleaned_news = state["cleaned_news"]
    if cleaned_news is not None:
        # 規則 2：新聞過舊
        news_date_str = cleaned_news.get("date")
        if news_date_str:
            try:
                news_date = date.fromisoformat(news_date_str)
                if (date.today() - news_date).days > NEWS_STALENESS_DAYS:
                    requires_news_refresh = True
            except ValueError:
                requires_news_refresh = True

        # 規則 3：數字不足
        mentioned_numbers = cleaned_news.get("mentioned_numbers", [])
        if not mentioned_numbers:
            requires_news_refresh = True

    data_sufficient = not requires_news_refresh and not requires_fundamental_update
    return data_sufficient, requires_news_refresh, requires_fundamental_update


def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷資料是否充分：snapshot 完整、新聞新鮮（≤7天）且含數字。"""
    data_sufficient, requires_news_refresh, requires_fundamental_update = _check_sufficiency(state)
    return {
        "data_sufficient": data_sufficient,
        "requires_news_refresh": requires_news_refresh,
        "requires_fundamental_update": requires_fundamental_update,
    }
```

同時在 `nodes.py` 頂部的 import 加入：
```python
from datetime import date
```

### Step 4: 執行確認通過

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

Expected: 所有測試 PASS

> 注意：builder.py 的 `_force_insufficient` 測試仍應通過，因為 `_force_insufficient=True` 時直接回傳 `{"data_sufficient": False}`，不呼叫 `judge_node`。

### Step 5: Commit

```bash
cd /Users/leo/Documents/work/financial-research/backend
git add src/ai_stock_sentinel/graph/nodes.py tests/test_graph_nodes.py
git commit -m "feat: implement judge_node data sufficiency logic"
```

---

## Task 3: 更新 builder.py，確保新 state 欄位有初始值

**Files:**
- Modify: `backend/tests/test_graph_builder.py`

builder.py 的 `_initial_state` helper（在測試檔案中）目前缺少新的兩個欄位。需要讓測試的初始 state 包含它們，否則 LangGraph invoke 可能因缺 key 報錯。

### Step 1: 更新 test_graph_builder.py 的 _initial_state

找到 `_initial_state` 函式，加入兩個新欄位：

```python
def _initial_state(symbol: str = "2330.TW") -> dict:
    return {
        "symbol": symbol,
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "cleaned_news": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
    }
```

### Step 2: 執行測試確認全部通過

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

Expected: 所有測試 PASS

### Step 3: Commit

```bash
cd /Users/leo/Documents/work/financial-research/backend
git add tests/test_graph_builder.py
git commit -m "test: add reason flags to graph builder test initial state"
```

---

## Task 4: 更新文件

**Files:**
- Modify: `docs/progress-tracker.md`
- Modify: `docs/implementation-task-breakdown.md`

### Step 1: 更新 progress-tracker.md

1. 高層完成度 Phase 2 改為：
   ```
   - **Phase 2（LangGraph 回圈）**：約 60%（骨架 + judge 邏輯完成）
   ```

2. Phase 2 區塊：
   ```markdown
   ### Phase 2：LangGraph
   - [x] 建立 LangGraph 狀態機（GraphState + 節點 stub + builder）
   - [x] loop guard（max_retries）骨架
   - [x] 完整性判斷節點（snapshot 缺失、新聞過舊、數字不足）
   - [ ] 新聞 RSS 自動抓取
   ```

### Step 2: 更新 implementation-task-breakdown.md

在 P2-2 的 DoD 下方加完成記錄：
```markdown
- **完成記錄**：judge_node 三條規則（snapshot 缺失、新聞過舊、數字不足）已實作，reason flags 加入 GraphState，測試覆蓋，2026-03-03
```

### Step 3: Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add docs/progress-tracker.md docs/implementation-task-breakdown.md
git commit -m "docs: update progress for judge_node logic (P2-2 done)"
```

---

## 驗收標準

```bash
cd /Users/leo/Documents/work/financial-research/backend
make test
```

全部 PASS，包含：
- `test_judge_node_sufficient_when_snapshot_and_fresh_news`
- `test_judge_node_insufficient_when_snapshot_missing`
- `test_judge_node_insufficient_when_news_stale`
- `test_judge_node_insufficient_when_no_mentioned_numbers`
- `test_judge_node_sufficient_when_no_news_provided`
- 既有所有測試（12 個）
