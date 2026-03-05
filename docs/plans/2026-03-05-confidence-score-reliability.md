# Confidence Score Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修正信心分數長期固定回傳 50 的問題，透過加權多條件取代 binary 規則、機構資料缺失時正確降維、並分拆資料完整度與訊號強度兩個維度。

**Architecture:**
- `confidence_scorer.py`：新增 `derive_technical_score()`（加權）、`derive_inst_score()`（加權）、重構 `adjust_confidence_by_divergence()` 為多維加權模型，並新增 `compute_confidence()` 整合兩個子分數。
- `nodes.py`：`score_node` 改為呼叫 `compute_confidence()`，輸出 `data_confidence`（資料完整度）與 `signal_confidence`（訊號強度）兩個欄位，同時維持向後相容的 `confidence_score`（= signal_confidence）。
- `state.py` / `api.py`：新增 `data_confidence` 與 `signal_confidence` 欄位。

**Tech Stack:** Python 3.11+, pytest, FastAPI/Pydantic

---

## 背景說明（給無脈絡的工程師）

### 問題根源

目前 `score_node` 的輸出幾乎固定為 50，原因是三條輸入訊號同時退化：

1. **`_derive_technical_signal`**（`nodes.py:135`）：bullish 條件需要 `close > ma5 > ma20 AND 50 ≤ RSI ≤ 70` 全部成立，任何一個不符合就退化為 `sideways`。
2. **機構資料**（`nodes.py:169`）：無 API key 時 `institutional_flow` 帶 `error` 欄位，`score_node` 讀到 `flow_label` 為 `None`，fallback 為 `"neutral"`。
3. **`adjust_confidence_by_divergence`**（`confidence_scorer.py`）：四條規則皆為精確命中（exact match），三維均為 `neutral/sideways` 時 adjustment = 0，結果永遠是 BASE_CONFIDENCE = 50。

### 修正策略

- **CS-1**：拆解 `_derive_technical_signal` 為三個獨立指標（RSI、BIAS、均線排列），各自計算分數後加權。
- **CS-2**：四條 binary 規則改為多維加權模型，partial match 可得部分調分。
- **CS-3**：機構資料缺失（帶 `error` 鍵）時，以 `"unknown"` 旗標排除，由剩餘兩維度計算。
- **CS-4**：分拆 `data_confidence`（資料完整度，0–100）與 `signal_confidence`（訊號強度，0–100）分開回傳，`confidence_score` 維持向後相容（= `signal_confidence`）。
- **CS-5**：補齊測試，所有新函式均有對應測試。

---

## Task 1：CS-1 — 拆解 `_derive_technical_signal` 為加權技術分

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`
- Test: `backend/tests/test_confidence_scorer.py`

### Step 1：在 `confidence_scorer.py` 新增 `derive_technical_score()`

在 `confidence_scorer.py` 的頂部 import 區後，`BASE_CONFIDENCE = 50` 之前，加入：

```python
from __future__ import annotations

BASE_CONFIDENCE = 50


def derive_technical_score(
    closes: list[float],
    rsi: float | None,
    bias: float | None,
) -> int:
    """由 RSI、BIAS、均線排列三個獨立訊號加權計算技術面分數。

    各訊號獨立評分（-1 / 0 / +1），再換算為 [30, 70] 分段。
    - RSI 30 以下 → -1（超賣偏空）；RSI 50–70 → +1（多頭動能）；其餘 0
    - BIAS < -5% → -1（乖離偏低/跌深）；BIAS > 5% → -1（過熱）；-5~5 → 0；特別地 -5 以內且RSI多頭 → 視RSI
    - 均線排列：close > ma5 > ma20 → +1；ma5 < ma20 → -1；其餘 0

    加權後：[-3, +3] 映射至 [30, 70]，中性為 50。
    資料不足（closes < 20）→ 回傳 50。
    """
    if len(closes) < 20:
        return 50

    from ai_stock_sentinel.analysis.context_generator import ma as calc_ma

    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    close = closes[-1]

    score = 0  # -3 to +3

    # RSI 訊號
    if rsi is not None:
        if rsi >= 50:
            score += 1   # 多頭動能
        elif rsi <= 30:
            score -= 1   # 超賣偏空

    # BIAS 訊號：過熱懲罰
    if bias is not None:
        if bias > 10:
            score -= 1   # 嚴重過熱，短線風險
        elif bias < -10:
            score -= 1   # 嚴重超跌
        elif -5 <= bias <= 5:
            score += 1   # 接近均線，健康

    # 均線排列訊號
    if ma5 is not None and ma20 is not None:
        if close > ma5 > ma20:
            score += 1   # 多頭排列
        elif ma5 < ma20:
            score -= 1   # 空頭排列

    # 映射 [-3, +3] → [30, 70]，step = 40/6 ≈ 6.67
    clamped = max(-3, min(3, score))
    return round(50 + clamped * (20 / 3))
```

### Step 2：先寫測試（TDD）

在 `test_confidence_scorer.py` 新增：

```python
from ai_stock_sentinel.analysis.confidence_scorer import derive_technical_score

# ─── derive_technical_score ───────────────────────────────────────────────────

def test_technical_score_insufficient_data():
    """資料不足（< 20 根）回傳 50"""
    assert derive_technical_score([100.0] * 15, rsi=60.0, bias=0.0) == 50


def test_technical_score_all_bullish():
    """三個訊號全多頭：score=+3 → 70"""
    closes = list(range(80, 101))  # 21 根，遞增排列，ma5 > ma20
    # rsi=60 → +1, bias=2.0 → +1, ma多頭排列 → +1
    result = derive_technical_score(closes, rsi=60.0, bias=2.0)
    assert result == 70


def test_technical_score_all_bearish():
    """三個訊號全空頭：score=-3 → 30"""
    closes = list(range(120, 99, -1))  # 21 根，遞減
    # rsi=25 → -1, bias=-12 → -1, ma空頭排列 → -1
    result = derive_technical_score(closes, rsi=25.0, bias=-12.0)
    assert result == 30


def test_technical_score_neutral():
    """三個訊號中性：score=0 → 50"""
    closes = [100.0] * 21  # 全平，ma5=ma20=close
    result = derive_technical_score(closes, rsi=45.0, bias=0.0)
    assert result == 50


def test_technical_score_partial_bullish():
    """只有 RSI 多頭：score=+1 → ~57"""
    closes = [100.0] * 21
    result = derive_technical_score(closes, rsi=55.0, bias=0.0)
    # score=+1（RSI）+1（bias 接近均線）= +2 → 50 + 2*(20/3) ≈ 63
    # 依實際計算
    assert 50 < result < 70
```

### Step 3：執行測試，確認失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py::test_technical_score_insufficient_data -v
```

Expected: `FAILED` with `ImportError` or `NameError`（function not yet exists）

### Step 4：實作 `derive_technical_score()`

按 Step 1 的程式碼加入 `confidence_scorer.py`。

### Step 5：執行所有新增測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py -k "technical_score" -v
```

Expected: 全部 PASS

### Step 6：確認既有測試不壞

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py -v
```

Expected: 全部 PASS

### Step 7：Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add backend/src/ai_stock_sentinel/analysis/confidence_scorer.py backend/tests/test_confidence_scorer.py
git commit -m "feat(cs-1): add derive_technical_score() with weighted RSI/BIAS/MA signals"
```

---

## Task 2：CS-2 — 四條規則改為多維加權模型

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`
- Test: `backend/tests/test_confidence_scorer.py`

### 背景

現有 `adjust_confidence_by_divergence()` 是 if-elif 精確命中，任何一維退化就掉回 adjustment=0。目標改為：各維度獨立評分，最後加總調分。

### Step 1：先寫測試（TDD）

在 `test_confidence_scorer.py` 新增 `test_weighted_adjustment_*` 系列（**不刪除**現有測試，現有測試需繼續通過）：

```python
# ─── 多維加權調分 ──────────────────────────────────────────────────────────────

def test_weighted_partial_positive_sentiment_only():
    """只有 sentiment=positive，inst=neutral，tech=sideways → 小幅加分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    # positive sentiment 貢獻 +5；其他 0
    assert score > 50
    assert score <= 60


def test_weighted_partial_inst_accumulation_only():
    """只有 inst=institutional_accumulation，其他 neutral → 小幅加分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="neutral",
        inst_flow="institutional_accumulation",
        technical_signal="sideways",
    )
    assert score > 50


def test_weighted_three_resonance_still_highest():
    """三維共振仍然是最高調分（>= +10）"""
    score_resonance, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    score_partial, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    assert score_resonance >= score_partial
    assert score_resonance >= 60  # 三維共振至少加 10


def test_weighted_distribution_still_negative():
    """利多出貨（positive + distribution）仍然是負調分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="sideways",
    )
    assert score < 50
    assert "出貨" in note or "警示" in note


def test_weighted_retail_chasing_still_negative():
    """散戶追高仍然是負調分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="neutral",
        inst_flow="retail_chasing",
        technical_signal="sideways",
    )
    assert score < 50
```

### Step 2：執行測試，確認 partial_positive 與 partial_inst 目前失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py::test_weighted_partial_positive_sentiment_only tests/test_confidence_scorer.py::test_weighted_partial_inst_accumulation_only -v
```

Expected: FAILED（當前 adjustment=0）

### Step 3：重構 `adjust_confidence_by_divergence()`

將現有的 if-elif 規則改為多維加權模型。**保留 function signature 完全不變**（backward compatible）：

```python
# Sentiment 分數對照
_SENTIMENT_SCORES = {
    "positive": +5,
    "negative": -5,
    "neutral": 0,
}

# Inst flow 分數對照
_INST_FLOW_SCORES = {
    "institutional_accumulation": +7,
    "distribution": -10,
    "retail_chasing": -8,
    "neutral": 0,
}

# Technical signal 分數對照
_TECH_SIGNAL_SCORES = {
    "bullish": +5,
    "bearish": -5,
    "sideways": 0,
}

# 三維共振 bonus
_THREE_RESONANCE_BONUS = +3  # 三維同向時額外加成

# 利多出貨 penalty（positive + distribution 的額外懲罰）
_BULLISH_DISTRIBUTION_PENALTY = -7


def adjust_confidence_by_divergence(
    base_score: int,
    news_sentiment: str,
    inst_flow: str,
    technical_signal: str,
) -> tuple[int, str]:
    """多維加權模型。回傳 (adjusted_score, cross_validation_note)。

    各維度獨立評分，加總後套用特殊情境加成/懲罰：
    - 三維共振（positive + institutional_accumulation + bullish）→ 額外 +3
    - 利多出貨（positive + distribution）→ 額外 -7
    """
    s = _SENTIMENT_SCORES.get(news_sentiment, 0)
    i = _INST_FLOW_SCORES.get(inst_flow, 0)
    t = _TECH_SIGNAL_SCORES.get(technical_signal, 0)
    adjustment = s + i + t

    note = ""

    # 特殊情境：三維共振
    if (
        news_sentiment == "positive"
        and inst_flow == "institutional_accumulation"
        and technical_signal == "bullish"
    ):
        adjustment += _THREE_RESONANCE_BONUS
        note = "三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高"

    # 特殊情境：利多出貨（加額外懲罰）
    elif news_sentiment == "positive" and inst_flow == "distribution":
        adjustment += _BULLISH_DISTRIBUTION_PENALTY
        note = "警示：基本面利多但法人同步出貨，疑似趁消息出貨，建議保守觀察"

    # 散戶追高提示
    elif inst_flow == "retail_chasing":
        note = "散戶追高風險：融資餘額異常激增，法人同步減碼，籌碼結構偏不健康"

    # 利空不跌提示
    elif news_sentiment == "negative" and technical_signal == "bullish":
        note = "利空不跌訊號：股價守穩支撐且技術偏強，逆勢佈局機會，需觀察持續性"

    score = max(0, min(100, base_score + adjustment))
    return score, note
```

> **注意**：現有測試斷言的分數（65, 30, 35, 60）現在可能改變，因為模型改了。需要更新測試期望值以反映新分數。更新原則：
> - 三維共振 = 50 + 5 + 7 + 5 + 3 = **70** → 更新 `test_three_dimensional_resonance` 期望值
> - 利多出貨 = 50 + 5 + (-10) + (-7) = **38** → 更新 `test_bullish_news_with_distribution`
> - 散戶追高 = 50 + 0 + (-8) + 5 = **47** → 更新 `test_retail_chasing`（tech=bullish）
> - 利空不跌 = 50 + (-5) + 0 + 5 = **50** → 更新 `test_bearish_news_but_price_holds`

### Step 4：更新舊有測試的期望值

在 `test_confidence_scorer.py` 更新以下斷言（舊分數 → 新分數）：

```python
# test_three_dimensional_resonance：65 → 70
assert score == 70

# test_bullish_news_with_distribution：30 → 38
assert score == 38

# test_retail_chasing：35 → 47
assert score == 47

# test_bearish_news_but_price_holds：60 → 50
assert score == 50

# test_priority_three_resonance_over_retail_chasing：
# positive + retail_chasing + bullish = 50 + 5 + (-8) + 5 = 52
assert score == 52

# test_priority_distribution_over_bearish_not_dropping：
# positive + distribution + bullish = 50 + 5 + (-10) + 5 + (-7) = 43
assert score == 43

# test_clamp_upper_bound：95 + 70-50=20 → 95 + 20 = 115 → clamp 100 ✓（仍 100）
# （三維共振 adjustment = 5+7+5+3 = 20）
assert score == 100  # 不變

# test_clamp_lower_bound：10 + 38-50=-12 → 10 + (-12) = -2 → clamp 0
# （positive + distribution + sideways = 5 + (-10) + (-7) + 0 = -12）
assert score == 0  # 不變

# test_custom_base_score：negative + neutral + bullish = -5 + 0 + 5 = 0 → 70 + 0 = 70
assert score == 70

# test_base_score_50_default_neutral：neutral + neutral + bearish = 0 + 0 + (-5) = -5 → 45
assert score == 45
# 同時更新斷言 note == "" → note 可能空或非空
```

> **特別注意 `test_default_no_adjustment`**：`neutral + neutral + sideways = 0 + 0 + 0 = 0`，仍為 50，note=""。此測試不需改。

### Step 5：執行所有測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py -v
```

Expected: 全部 PASS

### Step 6：Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add backend/src/ai_stock_sentinel/analysis/confidence_scorer.py backend/tests/test_confidence_scorer.py
git commit -m "feat(cs-2): replace binary rules with weighted multi-dimension model in adjust_confidence_by_divergence"
```

---

## Task 3：CS-3 — 機構資料缺失改以 `"unknown"` 排除

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Test: `backend/tests/test_confidence_scorer.py`
- Test: `backend/tests/test_nodes.py`（若有）

### Step 1：在 `confidence_scorer.py` 加入 `"unknown"` 處理

在 `_INST_FLOW_SCORES` dict 中加入：

```python
_INST_FLOW_SCORES = {
    "institutional_accumulation": +7,
    "distribution": -10,
    "retail_chasing": -8,
    "neutral": 0,
    "unknown": 0,  # 缺失時不貢獻分數，也不懲罰
}
```

同時在 `adjust_confidence_by_divergence` 的 `note` 生成邏輯中，新增：如果 `inst_flow == "unknown"`，且有特殊情境需要 inst_flow 觸發，則跳過。

（實際上由於 `unknown` 在 dict 中為 0，且 elif 鏈不會命中 `institutional_accumulation`/`distribution`/`retail_chasing`，天然排除。）

### Step 2：修改 `nodes.py` 的 `score_node`

在 `score_node` 中（`nodes.py:152`），讀取 `flow_label` 的邏輯改為：

```python
# flow_label — 無 API key 時 inst_flow_data 含 'error' 鍵，視為 unknown
inst_flow_data = state.get("institutional_flow")
inst_flow: str = "unknown"
if inst_flow_data and not inst_flow_data.get("error"):
    inst_flow = inst_flow_data.get("flow_label") or "unknown"
```

### Step 3：先寫測試（TDD）

在 `test_confidence_scorer.py` 新增：

```python
def test_unknown_inst_flow_excluded_from_score():
    """inst_flow=unknown 不貢獻調分，由其他兩維計算"""
    score_unknown, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    score_neutral, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    # unknown 與 neutral 行為應相同（均不觸發 inst 分數）
    assert score_unknown == score_neutral


def test_unknown_inst_no_three_resonance():
    """inst_flow=unknown 即使 sentiment=positive + tech=bullish，不觸發三維共振"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert "三維訊號共振" not in note
```

如果有 `test_nodes.py`，也新增 `score_node` 層的測試：

```python
# test_score_node_inst_with_error_treated_as_unknown
def test_score_node_inst_error_treated_as_unknown():
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "positive"},
        "institutional_flow": {"error": "NO_API_KEY"},  # 帶 error 鍵
        "snapshot": {"recent_closes": list(range(80, 105))},  # 25 根遞增
        "errors": [],
    }
    result = score_node(state)
    # 不應觸發三維共振，因為 inst 為 unknown
    assert "三維訊號共振" not in result.get("cross_validation_note", "")
```

### Step 4：執行測試，確認失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py::test_unknown_inst_flow_excluded_from_score tests/test_confidence_scorer.py::test_unknown_inst_no_three_resonance -v
```

Expected: FAILED（`"unknown"` 尚未加入 dict）

### Step 5：實作修改（confidence_scorer.py + nodes.py）

按 Step 1 & 2 修改。

### Step 6：執行所有測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py -v
```

Expected: 全部 PASS

### Step 7：Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add backend/src/ai_stock_sentinel/analysis/confidence_scorer.py backend/src/ai_stock_sentinel/graph/nodes.py backend/tests/test_confidence_scorer.py
git commit -m "feat(cs-3): treat missing institutional data as unknown, exclude from scoring"
```

---

## Task 4：CS-4 — 分拆 `data_confidence` 與 `signal_confidence`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`（新增 `compute_confidence()`）
- Modify: `backend/src/ai_stock_sentinel/graph/state.py`（新增欄位）
- Modify: `backend/src/ai_stock_sentinel/api.py`（新增欄位）
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`（`score_node` 輸出）
- Test: `backend/tests/test_confidence_scorer.py`

### Step 1：先定義語意

- **`data_confidence`**（資料完整度，0–100）：反映幾個維度的資料是否可用。
  - 三維完整（snapshot ≥ 20 根 + news sentiment 非預設 + inst 非 unknown）→ 100
  - 兩維可用 → 67
  - 只有一維可用 → 33
  - 無可用 → 0

- **`signal_confidence`**（訊號強度，0–100）：就現有資料的訊號強弱計算，即現在 `confidence_score` 的語意。

- **`confidence_score`**（向後相容）：= `signal_confidence`

### Step 2：在 `confidence_scorer.py` 新增 `compute_confidence()`

```python
def compute_confidence(
    base_score: int,
    news_sentiment: str,   # "positive" | "negative" | "neutral" | "unknown"
    inst_flow: str,        # "institutional_accumulation" | "distribution" | "retail_chasing" | "neutral" | "unknown"
    technical_signal: str, # "bullish" | "bearish" | "sideways" | "unknown"
) -> dict[str, int | str]:
    """計算 data_confidence、signal_confidence 與 cross_validation_note。

    Returns:
        {
            "data_confidence": int,      # 資料完整度 0-100
            "signal_confidence": int,    # 訊號強度 0-100（= 舊 confidence_score）
            "cross_validation_note": str,
        }
    """
    # 計算資料完整度
    available = sum([
        news_sentiment not in ("neutral",),  # sentiment 非預設
        inst_flow not in ("neutral", "unknown"),  # inst 有實際資料
        technical_signal not in ("sideways", "unknown"),  # technical 有方向
    ])
    data_confidence = round(available / 3 * 100)

    # 訊號強度：直接呼叫現有加權模型
    signal_confidence, note = adjust_confidence_by_divergence(
        base_score,
        news_sentiment=news_sentiment,
        inst_flow=inst_flow,
        technical_signal=technical_signal,
    )

    return {
        "data_confidence": data_confidence,
        "signal_confidence": signal_confidence,
        "cross_validation_note": note,
    }
```

### Step 3：先寫測試

```python
from ai_stock_sentinel.analysis.confidence_scorer import compute_confidence

def test_compute_confidence_all_available():
    """三維均有資料 → data_confidence=100"""
    result = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 100
    assert result["signal_confidence"] == 70  # 三維共振
    assert "三維訊號共振" in result["cross_validation_note"]


def test_compute_confidence_only_inst_missing():
    """inst=unknown → data_confidence=67（兩維可用）"""
    result = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 67


def test_compute_confidence_all_neutral():
    """三維均中性/退化 → data_confidence=0"""
    result = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert result["data_confidence"] == 0
    assert result["signal_confidence"] == 50
```

### Step 4：執行測試，確認失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py::test_compute_confidence_all_available -v
```

Expected: FAILED（`compute_confidence` not found）

### Step 5：實作 `compute_confidence()`

按 Step 2 的程式碼加入 `confidence_scorer.py`。

### Step 6：修改 `state.py`，新增兩個欄位

在 `GraphState` TypedDict 末尾加入：

```python
data_confidence: int | None
signal_confidence: int | None
```

### Step 7：修改 `api.py`，新增欄位

在 `AnalyzeResponse` class 中加入：

```python
data_confidence: int | None = None
signal_confidence: int | None = None
```

同時在 `analyze()` 函式的 `return AnalyzeResponse(...)` 中加入：

```python
data_confidence=result.get("data_confidence"),
signal_confidence=result.get("signal_confidence"),
```

### Step 8：修改 `nodes.py`，`score_node` 改呼叫 `compute_confidence()`

```python
from ai_stock_sentinel.analysis.confidence_scorer import BASE_CONFIDENCE, compute_confidence

# score_node 內部：
result_dict = compute_confidence(
    BASE_CONFIDENCE,
    news_sentiment=news_sentiment,
    inst_flow=inst_flow,
    technical_signal=technical_signal,
)

return {
    "confidence_score": result_dict["signal_confidence"],  # 向後相容
    "signal_confidence": result_dict["signal_confidence"],
    "data_confidence": result_dict["data_confidence"],
    "cross_validation_note": result_dict["cross_validation_note"],
}
```

同時修改 `api.py` 初始 `initial_state` 字典，加入：

```python
"data_confidence": None,
"signal_confidence": None,
```

### Step 9：執行所有測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/ -v
```

Expected: 全部 PASS

### Step 10：Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add backend/src/ai_stock_sentinel/analysis/confidence_scorer.py \
        backend/src/ai_stock_sentinel/graph/state.py \
        backend/src/ai_stock_sentinel/api.py \
        backend/src/ai_stock_sentinel/graph/nodes.py \
        backend/tests/test_confidence_scorer.py
git commit -m "feat(cs-4): split data_confidence and signal_confidence, maintain backward-compatible confidence_score"
```

---

## Task 5：CS-5 — 整合 `derive_technical_score` 進 `score_node`

**背景：** CS-1 新增的 `derive_technical_score()` 目前只是獨立函式，未接入 `score_node`。這個 task 讓 `_derive_technical_signal`（回傳 categorical）也利用新的加權邏輯。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Test: `backend/tests/test_nodes.py`（若無則新建）

### Step 1：修改 `_derive_technical_signal` 使用 `derive_technical_score` 輔助

```python
from ai_stock_sentinel.analysis.confidence_scorer import (
    BASE_CONFIDENCE, compute_confidence, derive_technical_score,
)
from ai_stock_sentinel.analysis.context_generator import calc_bias, calc_rsi, ma as calc_ma

def _derive_technical_signal(closes: list[float]) -> str:
    """由 close/ma5/ma20/RSI/BIAS 推導 technical_signal（多條件加權）。"""
    if len(closes) < 20:
        return "sideways"
    close = closes[-1]
    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    rsi = calc_rsi(closes, period=14)
    bias = calc_bias(close, ma20) if ma20 is not None else None

    tech_score = derive_technical_score(closes, rsi=rsi, bias=bias)

    if tech_score >= 60:
        return "bullish"
    if tech_score <= 40:
        return "bearish"
    return "sideways"
```

### Step 2：先寫測試（TDD）

在 `test_nodes.py`（或 `test_confidence_scorer.py`）新增：

```python
from ai_stock_sentinel.graph.nodes import _derive_technical_signal

def test_derive_technical_signal_bullish_trend():
    """遞增趨勢 + RSI 多頭 → bullish"""
    closes = list(range(80, 106))  # 26 根遞增，ma5 > ma20
    result = _derive_technical_signal(closes)
    assert result == "bullish"


def test_derive_technical_signal_bearish_trend():
    """遞減趨勢 + RSI 偏空 → bearish"""
    closes = list(range(120, 94, -1))  # 26 根遞減
    result = _derive_technical_signal(closes)
    assert result == "bearish"


def test_derive_technical_signal_flat():
    """全平 → sideways"""
    closes = [100.0] * 25
    result = _derive_technical_signal(closes)
    assert result == "sideways"


def test_derive_technical_signal_insufficient():
    """資料不足 → sideways"""
    result = _derive_technical_signal([100.0] * 15)
    assert result == "sideways"
```

### Step 3：執行測試，確認失敗

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/ -k "derive_technical_signal" -v
```

### Step 4：實作修改

按 Step 1 替換 `nodes.py` 中的 `_derive_technical_signal`。

### Step 5：執行所有測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/ -v
```

Expected: 全部 PASS

### Step 6：Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add backend/src/ai_stock_sentinel/graph/nodes.py backend/tests/
git commit -m "feat(cs-5): wire derive_technical_score into _derive_technical_signal for multi-condition weighting"
```

---

## Task 6：CS-5（續）— 補齊完整測試覆蓋

**Files:**
- Test: `backend/tests/test_confidence_scorer.py`
- Test: `backend/tests/test_nodes.py`

### Step 1：確認測試覆蓋所有 edge cases

執行覆蓋率報告：

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/test_confidence_scorer.py --cov=ai_stock_sentinel/analysis/confidence_scorer --cov-report=term-missing -v
```

### Step 2：補充缺漏測試

根據覆蓋率報告，補充未覆蓋的分支。常見需補充：

```python
def test_compute_confidence_one_signal_available():
    """只有 technical 有方向，其餘中性 → data_confidence=33"""
    result = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 33


def test_derive_technical_score_rsi_none():
    """RSI=None（資料不足計算）時仍可計算其他維度"""
    closes = [100.0] * 21
    result = derive_technical_score(closes, rsi=None, bias=2.0)
    assert isinstance(result, int)
    assert 0 <= result <= 100


def test_score_node_with_real_bullish_data():
    """整合測試：遞增 close 資料 + positive 新聞 → signal_confidence > 50"""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "cleaned_news": {"sentiment_label": "positive"},
        "institutional_flow": None,
        "snapshot": {"recent_closes": list(range(80, 106))},
        "errors": [],
    }
    result = score_node(state)
    assert result["signal_confidence"] > 50
    assert result["data_confidence"] is not None
    assert result["confidence_score"] == result["signal_confidence"]  # 向後相容
```

### Step 3：執行所有測試

```bash
cd /Users/leo/Documents/work/financial-research/backend
poetry run pytest tests/ -v --tb=short
```

Expected: 全部 PASS

### Step 4：最終 Commit

```bash
cd /Users/leo/Documents/work/financial-research
git add backend/tests/
git commit -m "test(cs-5): add edge case and integration tests for confidence scoring"
```

---

## 完成確認 Checklist

- [ ] `derive_technical_score()` 獨立函式，有測試
- [ ] `adjust_confidence_by_divergence()` 改為多維加權，partial match 可調分，現有測試更新
- [ ] `"unknown"` 機構資料在 `score_node` 層被正確標記
- [ ] `compute_confidence()` 整合函式，回傳 `data_confidence` + `signal_confidence` + `cross_validation_note`
- [ ] `GraphState`、`AnalyzeResponse`、`initial_state` 均含 `data_confidence` 與 `signal_confidence` 欄位
- [ ] `confidence_score` 仍向後相容（= `signal_confidence`）
- [ ] `_derive_technical_signal` 使用加權分數判斷訊號方向
- [ ] `poetry run pytest tests/ -v` 全數通過
