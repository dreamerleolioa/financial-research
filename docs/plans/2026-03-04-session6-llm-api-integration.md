# Session 6 Implementation Plan: Cost Guard + LLM Integration + API Fields

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add LLM cost safety guard (Task 7.6), wire real Anthropic LLM (Task 7.5), and expose new fields in the API response (Task 8).

**Architecture:** Three independent tasks executed in priority order. Task 7.6 is pure unit-testable logic added to `langchain_analyzer.py`. Task 7.5 adds Anthropic to `config.py` + `main.py` with graceful fallback. Task 8 extends `AnalyzeResponse` and the `/analyze` endpoint with new fields already present in `GraphState`.

**Tech Stack:** Python, FastAPI, LangChain, langchain-anthropic, pytest, python-dotenv

---

## Task 7.6: Cost Safety Guard in LangChainStockAnalyzer

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py:58-104`
- Create: `backend/tests/test_langchain_analyzer.py`

### Step 1: Write the failing tests

```python
# backend/tests/test_langchain_analyzer.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer
from ai_stock_sentinel.models import StockSnapshot


def _make_snapshot(**kwargs) -> StockSnapshot:
    defaults = dict(
        symbol="2330.TW",
        current_price=500.0,
        previous_close=498.0,
        day_open=499.0,
        day_high=502.0,
        day_low=497.0,
        volume=10_000_000,
        recent_closes=[490.0, 492.0, 495.0, 498.0, 500.0],
    )
    defaults.update(kwargs)
    return StockSnapshot(**defaults)


def test_cost_guard_does_not_trigger_for_normal_request():
    """Normal request (~640 tokens) must not raise."""
    mock_llm = MagicMock()
    mock_llm.invoke = MagicMock(return_value=MagicMock(content="ok"))

    # Patch chain invocation
    analyzer = LangChainStockAnalyzer(llm=mock_llm)
    snapshot = _make_snapshot()

    # Should not raise — use a short context
    try:
        analyzer._estimate_cost(
            snapshot=snapshot,
            technical_context="BIAS 正常，RSI 55。",
            institutional_context="法人小量買超。",
            confidence_score=60,
            cross_validation_note="訊號一致。",
        )
    except ValueError:
        pytest.fail("Cost guard incorrectly triggered for normal request")


def test_cost_guard_triggers_for_oversized_prompt():
    """Prompt > 333,000 chars (~>$1 USD) must raise ValueError with token count and cost."""
    mock_llm = MagicMock()
    analyzer = LangChainStockAnalyzer(llm=mock_llm)
    snapshot = _make_snapshot()

    huge_text = "A" * 400_000  # well above 333,000 char threshold

    with pytest.raises(ValueError) as exc_info:
        analyzer._estimate_cost(
            snapshot=snapshot,
            technical_context=huge_text,
            institutional_context="正常。",
            confidence_score=50,
            cross_validation_note="正常。",
        )

    msg = str(exc_info.value)
    assert "token" in msg.lower() or "tokens" in msg.lower(), f"Expected token count in error: {msg}"
    assert "$" in msg or "usd" in msg.lower() or "cost" in msg.lower(), f"Expected cost in error: {msg}"


def test_cost_guard_error_message_contains_numbers():
    """Error message must include estimated token count and dollar amount."""
    analyzer = LangChainStockAnalyzer(llm=None)
    snapshot = _make_snapshot()
    huge_text = "B" * 1_400_000  # ~350,000 tokens estimated

    with pytest.raises(ValueError) as exc_info:
        analyzer._estimate_cost(
            snapshot=snapshot,
            technical_context=huge_text,
            institutional_context="",
            confidence_score=50,
            cross_validation_note="",
        )

    msg = str(exc_info.value)
    # Should contain a number (token estimate) and a dollar sign
    import re
    assert re.search(r'\d+', msg), f"No number found in error message: {msg}"
    assert "$" in msg, f"No dollar sign in error message: {msg}"
```

### Step 2: Run tests to verify they fail

```bash
cd backend && source venv/bin/activate && PYTHONPATH=src python -m pytest tests/test_langchain_analyzer.py -v
```

Expected: `AttributeError: 'LangChainStockAnalyzer' object has no attribute '_estimate_cost'`

### Step 3: Implement `_estimate_cost` in `langchain_analyzer.py`

Add the method to `LangChainStockAnalyzer` class (insert before `analyze()`):

```python
_COST_PER_MILLION_INPUT_TOKENS = 3.0  # USD, claude-sonnet-4
_COST_THRESHOLD_USD = 1.0

def _estimate_cost(
    self,
    snapshot: StockSnapshot,
    *,
    technical_context: str | None,
    institutional_context: str | None,
    confidence_score: int | None,
    cross_validation_note: str | None,
) -> None:
    """Raise ValueError if estimated LLM input cost exceeds $1 USD.

    Estimation: total prompt char length / 4 = token count.
    Rate: $3 / 1M tokens (claude-sonnet-4 input).
    """
    combined = "".join([
        _SYSTEM_PROMPT,
        str(snapshot.symbol),
        str(snapshot.current_price),
        str(snapshot.previous_close),
        str(snapshot.day_open),
        str(snapshot.day_high),
        str(snapshot.day_low),
        str(snapshot.volume),
        str(snapshot.recent_closes),
        technical_context or "",
        institutional_context or "",
        str(confidence_score or 50),
        cross_validation_note or "",
    ])
    estimated_tokens = len(combined) / 4
    estimated_cost = (estimated_tokens / 1_000_000) * self._COST_PER_MILLION_INPUT_TOKENS

    if estimated_cost > self._COST_THRESHOLD_USD:
        raise ValueError(
            f"估算 input token 數：{int(estimated_tokens):,}，"
            f"預估費用：${estimated_cost:.4f} USD，"
            f"超過安全門檻 ${self._COST_THRESHOLD_USD} USD，已中止 LLM 呼叫。"
        )
```

Then call `self._estimate_cost(...)` inside `analyze()` right before building the chain (after the `self.llm is None` guard):

```python
self._estimate_cost(
    snapshot,
    technical_context=technical_context,
    institutional_context=institutional_context,
    confidence_score=confidence_score,
    cross_validation_note=cross_validation_note,
)
```

Full updated `analyze()` method body (insert the call at line ~89, right after the `self.llm is None` guard):

```python
def analyze(self, snapshot, *, technical_context=None, institutional_context=None,
            confidence_score=None, cross_validation_note=None):
    if not self._has_langchain():
        return "LangChain 尚未安裝，已保留分析介面。\n安裝 requirements 後可注入 BaseChatModel 啟用分析。"

    if self.llm is None:
        return "LLM 尚未設定（缺少 API Key 或模型），已保留 LangChain 分析介面。\n你可以注入任何 BaseChatModel 來啟用自動分析。"

    # Cost safety guard — raises ValueError if over $1 USD threshold
    self._estimate_cost(
        snapshot,
        technical_context=technical_context,
        institutional_context=institutional_context,
        confidence_score=confidence_score,
        cross_validation_note=cross_validation_note,
    )

    output_parsers = import_module("langchain_core.output_parsers")
    prompts = import_module("langchain_core.prompts")
    StrOutputParser = getattr(output_parsers, "StrOutputParser")
    ChatPromptTemplate = getattr(prompts, "ChatPromptTemplate")
    # ... rest of chain unchanged
```

### Step 4: Run tests to verify they pass

```bash
cd backend && source venv/bin/activate && PYTHONPATH=src python -m pytest tests/test_langchain_analyzer.py -v
```

Expected: 3 PASSED

### Step 5: Run full test suite

```bash
cd backend && source venv/bin/activate && PYTHONPATH=src python -m pytest tests/ -q
```

Expected: 131 passed (128 existing + 3 new)

### Step 6: Commit

```bash
git add backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py backend/tests/test_langchain_analyzer.py
git commit -m "feat: add LLM cost safety guard to LangChainStockAnalyzer (Task 7.6)"
```

---

## Task 7.5: Wire Real Anthropic LLM

**Files:**
- Modify: `backend/src/ai_stock_sentinel/config.py`
- Modify: `backend/src/ai_stock_sentinel/main.py`
- Modify: `backend/requirements.txt`

### Step 1: Check if `langchain-anthropic` is already installed

```bash
cd backend && source venv/bin/activate && pip show langchain-anthropic
```

Expected outcome A: shows package info → skip Step 2.
Expected outcome B: `WARNING: Package(s) not found` → do Step 2.

### Step 2 (if needed): Add to requirements and install

Add to `backend/requirements.txt`:
```
langchain-anthropic>=0.3.0
```

Then install:
```bash
cd backend && source venv/bin/activate && pip install langchain-anthropic
```

### Step 3: Extend `config.py` to read Anthropic settings

Replace the entire content of `backend/src/ai_stock_sentinel/config.py`:

```python
import os
from dataclasses import dataclass


@dataclass
class Settings:
    openai_api_key: str | None
    openai_model: str
    anthropic_api_key: str | None
    anthropic_model: str


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4"),
    )
```

### Step 4: Update `build_graph_deps()` in `main.py`

Replace the `llm = None` block in `build_graph_deps()`:

```python
def build_graph_deps():
    """Return (crawler, analyzer, rss_client, news_cleaner) ready to pass to build_graph()."""
    load_dotenv()
    settings = load_settings()

    llm = None

    # Prefer Anthropic (claude-sonnet-4) if key is present
    if settings.anthropic_api_key:
        try:
            from langchain_anthropic import ChatAnthropic
            llm = ChatAnthropic(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
                temperature=0.2,
            )
        except ImportError:
            pass  # langchain-anthropic not installed; fall through to OpenAI

    # Fallback to OpenAI if Anthropic not available
    if llm is None and settings.openai_api_key:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=0.2,
        )

    analyzer = LangChainStockAnalyzer(llm=llm)
    crawler = YFinanceCrawler()
    rss_client = RssNewsClient()
    news_cleaner = FinancialNewsCleaner(model=settings.anthropic_model if settings.anthropic_api_key else settings.openai_model)
    return crawler, analyzer, rss_client, news_cleaner
```

### Step 5: Run full test suite

```bash
cd backend && source venv/bin/activate && PYTHONPATH=src python -m pytest tests/ -q
```

Expected: same count as after Task 7.6 (131 passed). No new test failures — the fallback path is unchanged when no API key is set in the test environment.

### Step 6: Commit

```bash
git add backend/src/ai_stock_sentinel/config.py backend/src/ai_stock_sentinel/main.py backend/requirements.txt
git commit -m "feat: wire Anthropic claude-sonnet-4 LLM with OpenAI fallback (Task 7.5)"
```

---

## Task 8: Extend API Response with New Fields

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`

### Step 1: Write the failing API test

Add to the existing API test file (check its path first):

```bash
ls backend/tests/test_api*.py
```

Append to that test file:

```python
def test_analyze_response_includes_strategy_fields(test_client):
    """AnalyzeResponse must include strategy/confidence fields (even if None)."""
    resp = test_client.post("/analyze", json={"symbol": "2330.TW"})
    assert resp.status_code == 200
    data = resp.json()
    # These fields must be present in the response (can be None)
    for field in [
        "confidence_score",
        "cross_validation_note",
        "strategy_type",
        "entry_zone",
        "stop_loss",
        "holding_period",
    ]:
        assert field in data, f"Missing field '{field}' in response: {data.keys()}"
```

### Step 2: Run the failing test

```bash
cd backend && source venv/bin/activate && PYTHONPATH=src python -m pytest tests/test_api.py -v -k "strategy_fields"
```

Expected: FAIL — fields not present in response.

### Step 3: Update `AnalyzeResponse` in `api.py`

Add new optional fields to the class:

```python
class AnalyzeResponse(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis: str = ""
    cleaned_news: dict[str, Any] | None = None
    confidence_score: int | None = None
    cross_validation_note: str | None = None
    strategy_type: str | None = None
    entry_zone: str | None = None
    stop_loss: str | None = None
    holding_period: str | None = None

    class ErrorDetail(BaseModel):
        code: str
        message: str

    errors: list[ErrorDetail] = Field(default_factory=list)
```

### Step 4: Update `initial_state` in the `analyze()` endpoint

The `initial_state` dict must include ALL fields declared in `GraphState` TypedDict, otherwise LangGraph may raise a `KeyError`. Replace the `initial_state` block:

```python
initial_state: GraphState = {
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
    # Phase 3 fields
    "technical_context": None,
    "institutional_context": None,
    "institutional_flow": None,
    "strategy_type": None,
    "entry_zone": None,
    "stop_loss": None,
    "holding_period": None,
    "confidence_score": None,
    "cross_validation_note": None,
}
```

### Step 5: Update the `return AnalyzeResponse(...)` call to include new fields

```python
return AnalyzeResponse(
    snapshot=snapshot,
    analysis=analysis,
    cleaned_news=result.get("cleaned_news"),
    confidence_score=result.get("confidence_score"),
    cross_validation_note=result.get("cross_validation_note"),
    strategy_type=result.get("strategy_type"),
    entry_zone=result.get("entry_zone"),
    stop_loss=result.get("stop_loss"),
    holding_period=result.get("holding_period"),
    errors=response_errors,
)
```

### Step 6: Also update `initial_state` in `main.py` CLI path

In `main()` function, the `initial_state` dict also needs the Phase 3 fields:

```python
initial_state = {
    "symbol": args.symbol,
    "news_content": news_content,
    "snapshot": None,
    "analysis": None,
    "cleaned_news": None,
    "raw_news_items": None,
    "data_sufficient": False,
    "retry_count": 0,
    "errors": [],
    "requires_news_refresh": False,
    "requires_fundamental_update": False,
    # Phase 3 fields
    "technical_context": None,
    "institutional_context": None,
    "institutional_flow": None,
    "strategy_type": None,
    "entry_zone": None,
    "stop_loss": None,
    "holding_period": None,
    "confidence_score": None,
    "cross_validation_note": None,
}
```

### Step 7: Run full test suite

```bash
cd backend && source venv/bin/activate && PYTHONPATH=src python -m pytest tests/ -q
```

Expected: all pass (count = previous + 1 new API test)

### Step 8: Commit

```bash
git add backend/src/ai_stock_sentinel/api.py backend/src/ai_stock_sentinel/main.py
git commit -m "feat: add strategy/confidence fields to AnalyzeResponse and fix initial_state (Task 8)"
```

---

## Task 9: Update Progress Tracker

**Files:**
- Modify: `docs/progress-tracker.md`
- Modify: `docs/plans/2026-03-04-multi-dimension-analysis.md`

### Step 1: Mark tasks done in `progress-tracker.md`

Change these lines from `[ ]` to `[x]`:
- `Task 7.6` line
- `Task 7.5` line (and its sub-items)
- `Task 8` line and `Task 8.1`

Add Session 6 Handoff Snapshot to the plan file.

### Step 2: Commit

```bash
git add docs/progress-tracker.md docs/plans/2026-03-04-multi-dimension-analysis.md
git commit -m "docs: update progress tracker and handoff snapshot after Session 6"
```

---

## Summary

| Task | Files Changed | Tests Added |
|------|--------------|-------------|
| 7.6 Cost Guard | `langchain_analyzer.py` | 3 (cost guard unit tests) |
| 7.5 Anthropic LLM | `config.py`, `main.py`, `requirements.txt` | 0 (fallback path already tested) |
| 8 API Fields | `api.py`, `main.py` | 1 (API response fields) |
| 9 Docs | `progress-tracker.md`, plan file | - |

**Final expected test count:** 132 passed
