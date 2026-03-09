# Strategy Action Plan Deepening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 深化 `action_plan` 輸出，加入保本點位提示、分批操作量化文字、以及情境觸發的 If-Then 邏輯。

**Architecture:** 所有變更集中在 `strategy_generator.py` 的純 rule-based 函式中；`generate_action_plan` 新增 `resistance_20d` 與 `support_20d` 參數，回傳 dict 新增 `breakeven_note` 欄位，並調整 `action` 與 `momentum_expectation` 的文字。`strategy_node` 補傳新參數。測試檔同步更新 exact-match assertions。

**Tech Stack:** Python 3.12, pytest

---

## 背景：現有結構

```
backend/src/ai_stock_sentinel/analysis/strategy_generator.py
  - generate_action_plan(strategy_type, entry_zone, stop_loss, flow_label, confidence_score) -> dict
    回傳: {action, target_zone, defense_line, momentum_expectation}

backend/src/ai_stock_sentinel/graph/nodes.py
  - strategy_node()  ← 呼叫 generate_action_plan，需補傳新參數

backend/tests/test_strategy_generator.py
  ← 含多個 exact-match assertions 需更新
```

`GraphState` 已有 `resistance_20d: float | None` 與 `support_20d: float | None`，可直接取用。

---

## 三項需求說明

| # | 需求 | 變動欄位 | 說明 |
|---|------|----------|------|
| 1 | 動態停損保本提示 | `breakeven_note`（新增） | mid_term 時輸出保本提醒，其餘 `None` |
| 2 | 分批操作量化 | `action`（修改文字） | 各策略型態改為帶部位比例的描述 |
| 3 | If-Then 情境觸發 | `momentum_expectation`（擴充） | 在現有標籤後附加「若突破/跌破 XXX 則…」 |

---

## Task 1：更新 `action` 文字與新增 `breakeven_note`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/strategy_generator.py:162-191`
- Test: `backend/tests/test_strategy_generator.py`

### Step 1: 更新既有 exact-match 測試，反映新 action 文字

在 `test_strategy_generator.py` 找到以下三個測試，改成新文字：

```python
def test_generate_action_plan_returns_defensive_wait_action():
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="neutral",
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["action"] == "觀望（待訊號明確再試單）"


def test_generate_action_plan_mid_term_action():
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900-910",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=60,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["action"] == "分批佈局（首筆 30%）"


def test_generate_action_plan_short_term_action():
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="895-905",
        stop_loss="875",
        flow_label="neutral",
        confidence_score=70,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["action"] == "短線進場（首筆 50%，確認站穩再加碼）"
```

### Step 2: 新增 `breakeven_note` 測試

在測試檔末尾加入：

```python
def test_generate_action_plan_breakeven_note_mid_term():
    """mid_term → breakeven_note 包含保本提示"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=60,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["breakeven_note"] is not None
    assert "5%" in result["breakeven_note"]
    assert "入場成本" in result["breakeven_note"]


def test_generate_action_plan_breakeven_note_short_term_is_none():
    """short_term → breakeven_note 為 None"""
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="895",
        stop_loss="875",
        flow_label="neutral",
        confidence_score=70,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["breakeven_note"] is None


def test_generate_action_plan_breakeven_note_defensive_wait_is_none():
    """defensive_wait → breakeven_note 為 None"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="neutral",
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["breakeven_note"] is None
```

### Step 3: 更新 `test_generate_action_plan_contains_required_keys`

將 keys 集合加入 `breakeven_note`，並補傳新參數：

```python
def test_generate_action_plan_contains_required_keys():
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="895-905",
        stop_loss="875",
        flow_label="neutral",
        confidence_score=70,
        resistance_20d=None,
        support_20d=None,
    )
    assert set(result.keys()) == {
        "action", "target_zone", "defense_line", "momentum_expectation", "breakeven_note"
    }
```

### Step 4: 執行測試，確認全部失敗（TDD Red）

```bash
cd backend && python -m pytest tests/test_strategy_generator.py -v --tb=short 2>&1 | tail -30
```

預期：多個 FAILED，包含 `AssertionError` 在 action 文字與 `breakeven_note` 的 KeyError。

### Step 5: 更新 `generate_action_plan` 實作

修改 `backend/src/ai_stock_sentinel/analysis/strategy_generator.py` 的 `generate_action_plan` 函式：

```python
def generate_action_plan(
    strategy_type: str,
    entry_zone: str,
    stop_loss: str,
    flow_label: str | None,
    confidence_score: int | None,
    resistance_20d: float | None = None,
    support_20d: float | None = None,
) -> dict:
    """由 strategy_type / flow_label / confidence_score 推導 action_plan 各欄位。

    純 rule-based Python，不呼叫 LLM。
    """
    if strategy_type == "defensive_wait":
        action = "觀望（待訊號明確再試單）"
    elif strategy_type == "mid_term":
        action = "分批佈局（首筆 30%）"
    else:  # short_term
        action = "短線進場（首筆 50%，確認站穩再加碼）"

    breakeven_note = (
        "當帳面獲利達 5% 時，建議停損位上移至入場成本價" if strategy_type == "mid_term" else None
    )

    momentum = (
        "強（法人集結中）" if flow_label == "institutional_accumulation"
        else "弱（法人出貨中）" if flow_label == "distribution"
        else "中性"
    )

    return {
        "action": action,
        "target_zone": entry_zone,
        "defense_line": stop_loss,
        "momentum_expectation": momentum,
        "breakeven_note": breakeven_note,
    }
```

### Step 6: 執行測試，確認 Task 1 相關測試全部通過

```bash
cd backend && python -m pytest tests/test_strategy_generator.py -v --tb=short 2>&1 | tail -40
```

預期：Task 1 新增及修改的測試全部 PASSED；momentum If-Then 測試仍 FAILED（Task 2 尚未實作）。

### Step 7: Commit

```bash
cd backend
git add src/ai_stock_sentinel/analysis/strategy_generator.py tests/test_strategy_generator.py
git commit -m "feat: add breakeven_note and quantified action sizing to action_plan"
```

---

## Task 2：If-Then 情境觸發條件（擴充 `momentum_expectation`）

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/strategy_generator.py:162-191`
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py:462-468`
- Test: `backend/tests/test_strategy_generator.py`

### Step 1: 新增 If-Then 測試

在測試檔末尾加入：

```python
# ─── If-Then momentum_expectation 測試 ─────────────────────────────────────────

def test_momentum_expectation_accumulation_with_resistance():
    """institutional_accumulation + resistance_20d → 附帶突破提示"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=75,
        resistance_20d=950.0,
        support_20d=880.0,
    )
    assert "強（法人集結中）" in result["momentum_expectation"]
    assert "950.0" in result["momentum_expectation"]
    assert "突破" in result["momentum_expectation"]


def test_momentum_expectation_distribution_with_support():
    """distribution + support_20d → 附帶跌破提示"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="distribution",
        confidence_score=40,
        resistance_20d=950.0,
        support_20d=880.0,
    )
    assert "弱（法人出貨中）" in result["momentum_expectation"]
    assert "880.0" in result["momentum_expectation"]
    assert "跌破" in result["momentum_expectation"]
    assert "Bearish" in result["momentum_expectation"]


def test_momentum_expectation_neutral_with_both_levels():
    """neutral + 兩個價位 → 同時包含突破與跌破提示"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label=None,
        confidence_score=50,
        resistance_20d=950.0,
        support_20d=880.0,
    )
    assert "中性" in result["momentum_expectation"]
    assert "950.0" in result["momentum_expectation"]
    assert "880.0" in result["momentum_expectation"]


def test_momentum_expectation_accumulation_without_resistance():
    """institutional_accumulation 但 resistance_20d=None → 不含數字，只有基本標籤"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=75,
        resistance_20d=None,
        support_20d=None,
    )
    # 現有舊測試仍應通過（向後相容）
    assert result["momentum_expectation"] == "強（法人集結中）"


def test_momentum_expectation_distribution_without_support():
    """distribution 但 support_20d=None → 只有基本標籤"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="distribution",
        confidence_score=40,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "弱（法人出貨中）"


def test_momentum_expectation_neutral_without_levels():
    """neutral + 兩個 None → 只有基本標籤"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label=None,
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "中性"
```

### Step 2: 執行測試，確認新測試失敗

```bash
cd backend && python -m pytest tests/test_strategy_generator.py -k "momentum_expectation" -v --tb=short
```

預期：帶有 `950.0`、`突破`、`Bearish` 的斷言 FAILED。

### Step 3: 更新 `generate_action_plan` 實作中的 `momentum` 邏輯

將 Task 1 Step 5 實作的 `momentum` 變數改為：

```python
    if flow_label == "institutional_accumulation":
        base = "強（法人集結中）"
        if resistance_20d is not None:
            momentum = f"{base}；若突破 {resistance_20d:.1f} 壓力則動能轉強"
        else:
            momentum = base
    elif flow_label == "distribution":
        base = "弱（法人出貨中）"
        if support_20d is not None:
            momentum = f"{base}；若跌破 {support_20d:.1f} 支撐則轉向 Bearish"
        else:
            momentum = base
    else:
        base = "中性"
        if resistance_20d is not None and support_20d is not None:
            momentum = f"{base}；若突破 {resistance_20d:.1f} 則動能轉強，若跌破 {support_20d:.1f} 則轉弱"
        else:
            momentum = base
```

### Step 4: 執行所有策略生成器測試

```bash
cd backend && python -m pytest tests/test_strategy_generator.py -v --tb=short 2>&1 | tail -50
```

預期：全部 PASSED。

### Step 5: 更新 `strategy_node` 補傳新參數

修改 `backend/src/ai_stock_sentinel/graph/nodes.py` 的 `generate_action_plan` 呼叫（約第 462 行）：

```python
    action_plan = generate_action_plan(
        strategy_type=strategy["strategy_type"],
        entry_zone=strategy["entry_zone"],
        stop_loss=strategy["stop_loss"],
        flow_label=flow_label_for_tag,
        confidence_score=state.get("confidence_score"),
        resistance_20d=state.get("resistance_20d"),
        support_20d=state.get("support_20d"),
    )
```

### Step 6: 執行完整測試套件

```bash
cd backend && python -m pytest --tb=short 2>&1 | tail -20
```

預期：全部 PASSED，無 regression。

### Step 7: Commit

```bash
cd backend
git add src/ai_stock_sentinel/analysis/strategy_generator.py \
        src/ai_stock_sentinel/graph/nodes.py \
        tests/test_strategy_generator.py
git commit -m "feat: add if-then conditional triggers to momentum_expectation"
```

---

## 驗收標準

- [ ] `action_plan.action` 含部位比例（`首筆 30%` / `首筆 50%`）或觀望提示
- [ ] `action_plan.breakeven_note` 在 `mid_term` 時為非 `None` 字串，其餘為 `None`
- [ ] `action_plan.momentum_expectation` 在有價位資料時，附帶「若突破/跌破 XXX 則…」文字
- [ ] 無資料（`resistance_20d=None`、`support_20d=None`）時 `momentum_expectation` 維持舊有格式（向後相容）
- [ ] 全部 pytest 通過，無 regression
