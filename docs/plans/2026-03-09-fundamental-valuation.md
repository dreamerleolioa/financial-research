# 基本面估值工具 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 新增第四維度「基本面估值」——從 FinMind 抓取每季 EPS / 營收，計算 PE Band 與殖利率估值，產出 `fundamental_insight` 納入 LLM 分析與 API 回應。

**Architecture:** 採用與 `InstitutionalFlowProvider` 相同的 Provider 抽象模式，新增 `FundamentalProvider` 介面與 `FinMindFundamentalProvider` 實作；在 graph 中新增 `fetch_fundamental_node`（插在 `crawl` 之後、`judge` 之前），並在 `preprocess_node` 產出 `fundamental_context` 字串供 LLM Prompt 使用；`AnalysisDetail` 新增 `fundamental_insight` 欄位，`AnalyzeResponse` 新增 `fundamental_data` 欄位。

**Tech Stack:** Python 3.11, FinMind API (TaiwanStockFinancialStatements + TaiwanStockDividend), LangGraph, FastAPI, pytest

---

## 背景知識

### FinMind 相關 Dataset

- `TaiwanStockFinancialStatements`：每季財報，含 `EPS`（每股盈餘）、`Revenue`（營收）
- `TaiwanStockDividend`：現金股利（`CashEarningsDistribution`）、股票股利
- API endpoint：`https://api.finmindtrade.com/api/v4/data`，需帶 `token`（同 `FINMIND_API_TOKEN`）

### PE Band 計算邏輯

```
PE = current_price / (sum of last 4 quarters EPS)   # trailing twelve months (TTM)
PE_mean = mean(last 20 quarters PE)
PE_std  = std(last 20 quarters PE)
pe_band = "cheap" if PE < PE_mean - PE_std
          "expensive" if PE > PE_mean + PE_std
          "fair" otherwise
pe_percentile = percentile rank of current PE within last 20 quarters
```

### 殖利率估值

```
dividend_yield = annual_cash_dividend / current_price * 100
yield_signal = "high_yield" if dividend_yield >= 5.0
               "mid_yield"  if 3.0 <= dividend_yield < 5.0
               "low_yield"  otherwise
```

### 現有 Provider 模式（參考 institutional_flow）

- `interface.py`：定義 `FundamentalData` dataclass + `FundamentalProvider` Protocol + `FundamentalError` exception
- `finmind_provider.py`：實作 Provider，從 FinMind 拉資料、計算指標
- `tools.py`：`fetch_fundamental_data(symbol, current_price)` 高階函式，失敗回傳帶 `error` 鍵的 dict

---

## Task 1：`FundamentalData` 介面與資料結構

**Files:**
- Create: `backend/src/ai_stock_sentinel/data_sources/fundamental/__init__.py`
- Create: `backend/src/ai_stock_sentinel/data_sources/fundamental/interface.py`
- Create: `backend/tests/data_sources/fundamental/test_interface.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/data_sources/fundamental/test_interface.py
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError

def test_fundamental_data_defaults():
    d = FundamentalData(symbol="2330.TW")
    assert d.ttm_eps is None
    assert d.pe_current is None
    assert d.pe_band == "unknown"
    assert d.pe_percentile is None
    assert d.dividend_yield is None
    assert d.yield_signal == "unknown"
    assert d.source_provider == ""
    assert d.warnings == []

def test_fundamental_error_carries_code():
    err = FundamentalError(code="NO_DATA", message="empty", provider="FinMind")
    assert err.code == "NO_DATA"
    assert err.provider == "FinMind"
```

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/data_sources/fundamental/test_interface.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: 實作介面**

```python
# backend/src/ai_stock_sentinel/data_sources/fundamental/__init__.py
# (空白)

# backend/src/ai_stock_sentinel/data_sources/fundamental/interface.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class FundamentalData:
    symbol: str

    # EPS / 本益比
    ttm_eps: float | None = None          # 近四季合計 EPS
    pe_current: float | None = None       # 當前 PE（price / ttm_eps）
    pe_mean: float | None = None          # 歷史 PE 均值（近 20 季）
    pe_std: float | None = None           # 歷史 PE 標準差
    pe_band: str = "unknown"              # "cheap" | "fair" | "expensive" | "unknown"
    pe_percentile: float | None = None    # 當前 PE 在歷史分佈的百分位（0-100）

    # 殖利率
    annual_cash_dividend: float | None = None  # 最近年度現金股利合計
    dividend_yield: float | None = None        # 殖利率（%）
    yield_signal: str = "unknown"              # "high_yield" | "mid_yield" | "low_yield" | "unknown"

    # 元資料
    source_provider: str = ""
    warnings: list[str] = field(default_factory=list)


class FundamentalError(Exception):
    def __init__(self, code: str, message: str, provider: str = ""):
        super().__init__(message)
        self.code = code
        self.provider = provider


@runtime_checkable
class FundamentalProvider(Protocol):
    @property
    def name(self) -> str: ...

    def fetch(self, symbol: str, current_price: float) -> FundamentalData:
        """
        Raises:
            FundamentalError: 無法取得資料
        """
        ...
```

**Step 4: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/data_sources/fundamental/test_interface.py -v
```
Expected: 2 passed

**Step 5: Commit**

```bash
cd backend && git add src/ai_stock_sentinel/data_sources/fundamental/ tests/data_sources/fundamental/test_interface.py
git commit -m "feat: add FundamentalData interface and FundamentalError"
```

---

## Task 2：`FinMindFundamentalProvider` 實作

**Files:**
- Create: `backend/src/ai_stock_sentinel/data_sources/fundamental/finmind_provider.py`
- Create: `backend/tests/data_sources/fundamental/test_finmind_provider.py`

**Step 1: 寫失敗測試（mock FinMind API）**

```python
# backend/tests/data_sources/fundamental/test_finmind_provider.py
from unittest.mock import MagicMock, patch
import pytest
from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError

MOCK_FINANCIAL_ROWS = [
    # 近 8 季 EPS（簡化）
    {"date": "2024-03-31", "type": "EPS", "value": 8.5},
    {"date": "2024-06-30", "type": "EPS", "value": 9.2},
    {"date": "2024-09-30", "type": "EPS", "value": 10.1},
    {"date": "2024-12-31", "type": "EPS", "value": 11.3},
    {"date": "2023-03-31", "type": "EPS", "value": 7.0},
    {"date": "2023-06-30", "type": "EPS", "value": 7.5},
    {"date": "2023-09-30", "type": "EPS", "value": 8.0},
    {"date": "2023-12-31", "type": "EPS", "value": 8.2},
]

MOCK_DIVIDEND_ROWS = [
    {"date": "2024-07-01", "CashEarningsDistribution": 16.0},
    {"date": "2023-07-01", "CashEarningsDistribution": 13.0},
]

def _make_provider(token="fake-token"):
    return FinMindFundamentalProvider(api_token=token)

@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_fetch_calculates_ttm_eps(mock_fetch):
    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return MOCK_FINANCIAL_ROWS
        return MOCK_DIVIDEND_ROWS
    mock_fetch.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=1000.0)

    assert isinstance(result, FundamentalData)
    # TTM = 最近 4 季：8.5+9.2+10.1+11.3 = 39.1
    assert result.ttm_eps == pytest.approx(39.1, abs=0.01)
    assert result.pe_current == pytest.approx(1000.0 / 39.1, abs=0.1)

@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_pe_band_cheap(mock_fetch):
    # 設定歷史 PE 均值=30, std=5；當前 PE=20 → cheap
    rows = [{"date": f"202{i}-03-31", "type": "EPS", "value": 2.0} for i in range(5)]
    rows += [{"date": f"202{i}-06-30", "type": "EPS", "value": 2.0} for i in range(5)]
    rows += [{"date": f"202{i}-09-30", "type": "EPS", "value": 2.0} for i in range(5)]
    rows += [{"date": f"202{i}-12-31", "type": "EPS", "value": 2.0} for i in range(5)]

    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return rows
        return []
    mock_fetch.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=100.0)
    # ttm_eps = 8.0, pe = 12.5
    assert result.pe_current == pytest.approx(100.0 / 8.0, abs=0.1)
    assert result.pe_band in ("cheap", "fair", "expensive", "unknown")

@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_dividend_yield_high(mock_fetch):
    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return MOCK_FINANCIAL_ROWS
        return MOCK_DIVIDEND_ROWS
    mock_fetch.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=200.0)
    # annual dividend = 16.0, yield = 8% → high_yield
    assert result.dividend_yield == pytest.approx(16.0 / 200.0 * 100, abs=0.01)
    assert result.yield_signal == "high_yield"

@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_raises_when_no_eps_data(mock_fetch):
    mock_fetch.return_value = []
    provider = _make_provider()
    with pytest.raises(FundamentalError) as exc_info:
        provider.fetch("2330.TW", current_price=1000.0)
    assert exc_info.value.code == "FINMIND_NO_EPS_DATA"
```

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/data_sources/fundamental/test_finmind_provider.py -v
```
Expected: `ModuleNotFoundError`

**Step 3: 實作 Provider**

```python
# backend/src/ai_stock_sentinel/data_sources/fundamental/finmind_provider.py
from __future__ import annotations
import logging
import statistics
from datetime import date, timedelta

from ai_stock_sentinel.data_sources.fundamental.interface import (
    FundamentalData, FundamentalError,
)

logger = logging.getLogger(__name__)
_FINMIND_API = "https://api.finmindtrade.com/api/v4/data"


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class FinMindFundamentalProvider:
    name = "FinMindFundamental"

    def __init__(self, api_token: str = "") -> None:
        self._token = api_token

    def _fetch_dataset(self, dataset: str, stock_id: str, start_date: str, end_date: str) -> list[dict]:
        try:
            import requests
        except ImportError as e:
            raise FundamentalError("MISSING_DEPENDENCY", "requests 未安裝", self.name) from e

        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date,
            "end_date": end_date,
            "token": self._token,
        }
        resp = requests.get(_FINMIND_API, params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", [])

    def fetch(self, symbol: str, current_price: float) -> FundamentalData:
        stock_id = symbol.split(".")[0]
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=365 * 6)).isoformat()  # 6 年抓 20+ 季
        warnings: list[str] = []

        # ---- EPS ----
        fin_rows = self._fetch_dataset(
            dataset="TaiwanStockFinancialStatements",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        eps_rows = [r for r in fin_rows if r.get("type") == "EPS"]
        eps_rows.sort(key=lambda r: r.get("date", ""))

        if not eps_rows:
            raise FundamentalError(
                code="FINMIND_NO_EPS_DATA",
                message=f"FinMind EPS 資料為空（symbol={symbol}）",
                provider=self.name,
            )

        eps_values = [_safe_float(r.get("value")) for r in eps_rows]
        eps_values = [v for v in eps_values if v is not None]

        ttm_eps: float | None = None
        pe_current: float | None = None
        pe_mean: float | None = None
        pe_std: float | None = None
        pe_band = "unknown"
        pe_percentile: float | None = None

        if len(eps_values) >= 4:
            ttm_eps = sum(eps_values[-4:])
            if ttm_eps and ttm_eps > 0:
                pe_current = current_price / ttm_eps

                # 歷史 PE：逐季滑動（每 4 季一組）
                historical_pes: list[float] = []
                for i in range(4, len(eps_values) + 1):
                    window_eps = sum(eps_values[i - 4:i])
                    if window_eps and window_eps > 0:
                        historical_pes.append(current_price / window_eps)

                if len(historical_pes) >= 4:
                    pe_mean = statistics.mean(historical_pes)
                    pe_std = statistics.stdev(historical_pes) if len(historical_pes) >= 2 else 0.0

                    if pe_std and pe_std > 0:
                        if pe_current < pe_mean - pe_std:
                            pe_band = "cheap"
                        elif pe_current > pe_mean + pe_std:
                            pe_band = "expensive"
                        else:
                            pe_band = "fair"
                    else:
                        pe_band = "fair"

                    below = sum(1 for p in historical_pes if p <= pe_current)
                    pe_percentile = below / len(historical_pes) * 100
        else:
            warnings.append("EPS 季數不足 4 季，無法計算 TTM EPS")

        # ---- 股利 ----
        div_rows = self._fetch_dataset(
            dataset="TaiwanStockDividend",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        div_rows.sort(key=lambda r: r.get("date", ""), reverse=True)

        annual_cash_dividend: float | None = None
        dividend_yield: float | None = None
        yield_signal = "unknown"

        if div_rows:
            # 取最近一筆年度現金股利
            latest_cash = _safe_float(div_rows[0].get("CashEarningsDistribution"))
            if latest_cash is not None:
                annual_cash_dividend = latest_cash
                dividend_yield = annual_cash_dividend / current_price * 100
                if dividend_yield >= 5.0:
                    yield_signal = "high_yield"
                elif dividend_yield >= 3.0:
                    yield_signal = "mid_yield"
                else:
                    yield_signal = "low_yield"
        else:
            warnings.append("FinMind: 股利資料為空")

        return FundamentalData(
            symbol=symbol,
            ttm_eps=ttm_eps,
            pe_current=pe_current,
            pe_mean=pe_mean,
            pe_std=pe_std,
            pe_band=pe_band,
            pe_percentile=pe_percentile,
            annual_cash_dividend=annual_cash_dividend,
            dividend_yield=dividend_yield,
            yield_signal=yield_signal,
            source_provider=self.name,
            warnings=warnings,
        )
```

**Step 4: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/data_sources/fundamental/test_finmind_provider.py -v
```
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/ai_stock_sentinel/data_sources/fundamental/finmind_provider.py tests/data_sources/fundamental/test_finmind_provider.py
git commit -m "feat: add FinMindFundamentalProvider with PE band and yield calculation"
```

---

## Task 3：`fetch_fundamental_data` 工具函式

**Files:**
- Create: `backend/src/ai_stock_sentinel/data_sources/fundamental/tools.py`
- Create: `backend/tests/data_sources/fundamental/test_tools.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/data_sources/fundamental/test_tools.py
from unittest.mock import patch, MagicMock
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError


def test_returns_dict_on_success():
    mock_data = FundamentalData(symbol="2330.TW", ttm_eps=39.1, pe_current=25.6, pe_band="fair")
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.FinMindFundamentalProvider"
    ) as MockProvider:
        MockProvider.return_value.fetch.return_value = mock_data
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert isinstance(result, dict)
    assert result["ttm_eps"] == 39.1
    assert result["pe_band"] == "fair"
    assert "error" not in result


def test_returns_error_dict_on_failure():
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.FinMindFundamentalProvider"
    ) as MockProvider:
        MockProvider.return_value.fetch.side_effect = FundamentalError("NO_DATA", "empty")
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert "error" in result
    assert result["error"] == "NO_DATA"


def test_never_raises():
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.FinMindFundamentalProvider"
    ) as MockProvider:
        MockProvider.return_value.fetch.side_effect = RuntimeError("unexpected")
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert "error" in result
```

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/data_sources/fundamental/test_tools.py -v
```

**Step 3: 實作工具函式**

```python
# backend/src/ai_stock_sentinel/data_sources/fundamental/tools.py
from __future__ import annotations
import logging
import os
from dataclasses import asdict

from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalError

logger = logging.getLogger(__name__)


def fetch_fundamental_data(symbol: str, current_price: float) -> dict:
    """高階工具函式：取得基本面估值資料，失敗時回傳帶 error 鍵的 dict，不拋例外。"""
    token = os.environ.get("FINMIND_API_TOKEN", "")
    provider = FinMindFundamentalProvider(api_token=token)
    try:
        data = provider.fetch(symbol, current_price)
        return asdict(data)
    except FundamentalError as e:
        logger.warning("FundamentalProvider error [%s]: %s", e.code, e)
        return {"error": e.code, "message": str(e), "symbol": symbol}
    except Exception as e:
        logger.exception("Unexpected error in fetch_fundamental_data")
        return {"error": "FUNDAMENTAL_UNKNOWN_ERROR", "message": str(e), "symbol": symbol}
```

**Step 4: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/data_sources/fundamental/test_tools.py -v
```
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/ai_stock_sentinel/data_sources/fundamental/tools.py tests/data_sources/fundamental/test_tools.py
git commit -m "feat: add fetch_fundamental_data tool wrapper"
```

---

## Task 4：`fundamental_context` 敘事產生器

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/context_generator.py`
- Create: `backend/tests/analysis/test_fundamental_narrative.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/analysis/test_fundamental_narrative.py
from ai_stock_sentinel.analysis.context_generator import generate_fundamental_context

def test_full_data_generates_narrative():
    data = {
        "ttm_eps": 39.1,
        "pe_current": 25.6,
        "pe_mean": 22.0,
        "pe_std": 4.0,
        "pe_band": "expensive",
        "pe_percentile": 75.0,
        "annual_cash_dividend": 16.0,
        "dividend_yield": 1.6,
        "yield_signal": "low_yield",
    }
    result = generate_fundamental_context(data)
    assert "25.6" in result or "25" in result
    assert "expensive" in result or "偏貴" in result
    assert "1.6" in result or "低" in result

def test_empty_data_returns_placeholder():
    result = generate_fundamental_context({})
    assert "基本面" in result  # 有提示說明資料不足

def test_error_dict_returns_placeholder():
    result = generate_fundamental_context({"error": "NO_DATA"})
    assert "基本面" in result

def test_none_returns_placeholder():
    result = generate_fundamental_context(None)
    assert "基本面" in result
```

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/analysis/test_fundamental_narrative.py -v
```

**Step 3: 在 `context_generator.py` 新增函式**

在檔案末尾新增（不修改任何現有函式）：

```python
# ─── 基本面敘事 ────────────────────────────────────────────────────────────────

def generate_fundamental_context(fund: dict | None) -> str:
    """根據 FundamentalData dict 產出基本面估值敘事。

    fund 可為 None、空 dict、或含 error 鍵的 dict，均安全處理。
    """
    if not fund or fund.get("error"):
        return "基本面資料不足或抓取失敗，無法產出估值敘事。"

    parts: list[str] = []

    # PE
    pe = fund.get("pe_current")
    pe_band = fund.get("pe_band", "unknown")
    pe_pct = fund.get("pe_percentile")
    pe_mean = fund.get("pe_mean")

    _band_map = {"cheap": "偏低（便宜）", "fair": "合理", "expensive": "偏高（昂貴）", "unknown": "未知"}
    if pe is not None:
        parts.append(f"當前本益比（PE）{pe:.1f} 倍，估值位階{_band_map.get(pe_band, pe_band)}")
    if pe_mean is not None:
        parts.append(f"歷史 PE 均值 {pe_mean:.1f} 倍")
    if pe_pct is not None:
        parts.append(f"PE 百分位 {pe_pct:.0f}%（高於 {pe_pct:.0f}% 的歷史觀測）")

    # 殖利率
    dy = fund.get("dividend_yield")
    yield_sig = fund.get("yield_signal", "unknown")
    _yield_map = {"high_yield": "高殖利率（≥5%）", "mid_yield": "中殖利率（3–5%）", "low_yield": "低殖利率（<3%）", "unknown": "未知"}
    if dy is not None:
        parts.append(f"現金殖利率 {dy:.2f}%，屬{_yield_map.get(yield_sig, yield_sig)}")

    # TTM EPS
    ttm = fund.get("ttm_eps")
    if ttm is not None:
        parts.append(f"近四季合計 EPS {ttm:.2f} 元")

    if not parts:
        return "基本面資料欄位不完整，敘事略過。"

    return "【基本面估值】" + "；".join(parts) + "。"
```

**Step 4: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/analysis/test_fundamental_narrative.py -v
```
Expected: 4 passed

**Step 5: Commit**

```bash
git add src/ai_stock_sentinel/analysis/context_generator.py tests/analysis/test_fundamental_narrative.py
git commit -m "feat: add generate_fundamental_context narrative function"
```

---

## Task 5：Graph 整合——`fetch_fundamental_node` + State + Builder

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/state.py`
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Modify: `backend/src/ai_stock_sentinel/graph/builder.py`
- Create: `backend/tests/graph/test_fetch_fundamental_node.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/graph/test_fetch_fundamental_node.py
from ai_stock_sentinel.graph.nodes import fetch_fundamental_node

def _base_state():
    return {
        "symbol": "2330.TW",
        "snapshot": {"current_price": 1785.0},
        "fundamental_data": None,
        "fundamental_context": None,
        "errors": [],
    }

def test_writes_fundamental_data_on_success():
    mock_result = {"ttm_eps": 39.1, "pe_band": "fair"}

    def mock_fetcher(symbol, current_price):
        return mock_result

    result = fetch_fundamental_node(_base_state(), fetcher=mock_fetcher)
    assert result["fundamental_data"] == mock_result
    assert result["fundamental_context"]  # 非空字串


def test_handles_fetcher_error_dict():
    def mock_fetcher(symbol, current_price):
        return {"error": "NO_DATA", "message": "empty"}

    result = fetch_fundamental_node(_base_state(), fetcher=mock_fetcher)
    assert result["fundamental_data"]["error"] == "NO_DATA"
    assert "基本面" in result["fundamental_context"]


def test_handles_missing_snapshot():
    state = _base_state()
    state["snapshot"] = None

    def mock_fetcher(symbol, current_price):
        return {"ttm_eps": 10.0}

    result = fetch_fundamental_node(state, fetcher=mock_fetcher)
    # snapshot 缺失時 current_price=0，不拋例外
    assert "fundamental_data" in result
```

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/graph/test_fetch_fundamental_node.py -v
```

**Step 3: 更新 `state.py`**

在 `GraphState` TypedDict 中加入兩個新欄位：

```python
# 在 action_plan: dict[str, Any] | None 之後新增：
fundamental_data: dict[str, Any] | None
fundamental_context: str | None
```

**Step 4: 更新 `nodes.py`**

1. 在 import 區塊新增：
```python
from ai_stock_sentinel.analysis.context_generator import generate_fundamental_context
```

2. 在 `fetch_institutional_node` 之後新增：

```python
def fetch_fundamental_node(
    state: GraphState,
    *,
    fetcher: Callable[[str, float], dict[str, Any]],
) -> dict[str, Any]:
    """呼叫 fetcher 取得基本面估值資料，並產出 fundamental_context 敘事字串。"""
    symbol = state["symbol"]
    snapshot = state.get("snapshot") or {}
    current_price = float(snapshot.get("current_price") or 0)
    fund = fetcher(symbol, current_price)
    context = generate_fundamental_context(fund)
    return {"fundamental_data": fund, "fundamental_context": context}
```

**Step 5: 更新 `builder.py`**

1. import 新增：
```python
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
```

2. `build_graph` 簽名新增參數：
```python
fundamental_fetcher: Callable[[str, float], dict[str, Any]] | None = None,
```

3. 在 `_institutional_fetcher` 設定後新增：
```python
_fundamental_fetcher = fundamental_fetcher or fetch_fundamental_data
```

4. 新增節點（在 `fetch_institutional` 之後）：
```python
graph.add_node("fetch_fundamental", partial(fetch_fundamental_node, fetcher=_fundamental_fetcher))
```

5. 更新邊：將 `fetch_institutional → judge` 改為：
```python
graph.add_edge("fetch_institutional", "fetch_fundamental")
graph.add_edge("fetch_fundamental", "judge")
```

**Step 6: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/graph/test_fetch_fundamental_node.py -v
```
Expected: 3 passed

**Step 7: 跑全套測試確認沒有回歸**

```bash
cd backend && ./venv/bin/pytest --tb=short -q
```
Expected: all passed (數字比原本多 3)

**Step 8: Commit**

```bash
git add src/ai_stock_sentinel/graph/ tests/graph/test_fetch_fundamental_node.py
git commit -m "feat: add fetch_fundamental_node to graph pipeline"
```

---

## Task 6：`AnalysisDetail` 新增 `fundamental_insight` + LLM Prompt 更新

**Files:**
- Modify: `backend/src/ai_stock_sentinel/models.py`
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`
- Create: `backend/tests/analysis/test_fundamental_insight_parse.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/analysis/test_fundamental_insight_parse.py
import json
from ai_stock_sentinel.analysis.langchain_analyzer import LangChainStockAnalyzer

def test_parse_analysis_includes_fundamental_insight():
    raw = json.dumps({
        "summary": "台積電估值偏高，建議觀望。",
        "risks": ["PE 偏高"],
        "technical_signal": "bearish",
        "institutional_flow": "neutral",
        "sentiment_label": "neutral",
        "tech_insight": "技術面偏空",
        "inst_insight": "法人觀望",
        "news_insight": "消息中性",
        "fundamental_insight": "PE 25.6 倍，位於歷史 75 百分位，估值偏貴。",
        "final_verdict": "多維偏空",
    })
    detail = LangChainStockAnalyzer._parse_analysis(raw)
    assert detail.fundamental_insight == "PE 25.6 倍，位於歷史 75 百分位，估值偏貴。"

def test_parse_analysis_fundamental_insight_defaults_to_none():
    raw = json.dumps({
        "summary": "test",
        "risks": [],
        "technical_signal": "sideways",
    })
    detail = LangChainStockAnalyzer._parse_analysis(raw)
    assert detail.fundamental_insight is None
```

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/analysis/test_fundamental_insight_parse.py -v
```

**Step 3: 更新 `models.py`**

在 `AnalysisDetail` 的 `final_verdict: str | None = None` 之後新增：

```python
fundamental_insight: str | None = None
```

**Step 4: 更新 `langchain_analyzer.py`**

① System Prompt：在 `news_insight` 規範之後、`final_verdict` 之前新增：
```
- fundamental_insight：僅參考基本面資料（PE 位階、殖利率、EPS 趨勢）；禁止提及技術指標或法人動向
```

② JSON schema 範例中新增欄位：
```json
"fundamental_insight": "基本面估值分析段落（若無資料則填 null）",
```

③ `_parse_analysis()` 的 `AnalysisDetail(...)` 呼叫中新增：
```python
fundamental_insight=data.get("fundamental_insight"),
```

**Step 5: 更新 `_HUMAN_PROMPT`**

在 `【籌碼面敘事】` 區塊之後新增：

```
【基本面估值】
{fundamental_context}
```

同時更新 `analyze()` 方法簽名新增 `fundamental_context: str | None = None` 參數，並在 `_HUMAN_PROMPT.format(...)` 中傳入：
```python
fundamental_context=fundamental_context or "（本次無基本面資料）",
```

**Step 6: 更新 `analyze_node`（`nodes.py`）**

在 `analyze_node` 的 `analyzer.analyze(...)` 呼叫中傳入：
```python
fundamental_context=state.get("fundamental_context"),
```

**Step 7: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/analysis/test_fundamental_insight_parse.py -v
```
Expected: 2 passed

**Step 8: Commit**

```bash
git add src/ai_stock_sentinel/models.py src/ai_stock_sentinel/analysis/langchain_analyzer.py src/ai_stock_sentinel/graph/nodes.py tests/analysis/test_fundamental_insight_parse.py
git commit -m "feat: add fundamental_insight to AnalysisDetail and LLM prompt"
```

---

## Task 7：`AnalyzeResponse` 新增 `fundamental_data` + API 整合

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Modify: `backend/src/ai_stock_sentinel/main.py`（initial_state 補欄位）
- Create: `backend/tests/api/test_fundamental_api_fields.py`

**Step 1: 寫失敗測試**

```python
# backend/tests/api/test_fundamental_api_fields.py
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from ai_stock_sentinel.api import app

client = TestClient(app)


def _mock_graph_result():
    return {
        "snapshot": {"symbol": "2330.TW", "current_price": 1785.0},
        "analysis": "test",
        "analysis_detail": None,
        "cleaned_news": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "confidence_score": 50,
        "signal_confidence": 50,
        "data_confidence": 100,
        "cross_validation_note": None,
        "strategy_type": "defensive_wait",
        "entry_zone": "1747–1865",
        "stop_loss": "1712",
        "holding_period": "觀望",
        "action_plan_tag": "neutral",
        "action_plan": None,
        "institutional_flow": None,
        "raw_news_items": [],
        "errors": [],
        "rsi14": None,
        "high_20d": None,
        "low_20d": None,
        "support_20d": None,
        "resistance_20d": None,
        "fundamental_data": {
            "symbol": "2330.TW",
            "ttm_eps": 39.1,
            "pe_current": 25.6,
            "pe_band": "fair",
            "pe_percentile": 60.0,
            "dividend_yield": 1.8,
            "yield_signal": "low_yield",
        },
        "fundamental_context": "【基本面估值】當前 PE 25.6 倍，估值合理。",
    }


def test_analyze_response_includes_fundamental_data():
    with patch("ai_stock_sentinel.api.get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = _mock_graph_result()
        mock_get_graph.return_value = mock_graph

        resp = client.post("/analyze", json={"symbol": "2330.TW"})

    assert resp.status_code == 200
    body = resp.json()
    assert "fundamental_data" in body
    fd = body["fundamental_data"]
    assert fd["pe_band"] == "fair"
    assert fd["ttm_eps"] == pytest.approx(39.1)
```

注意：在測試檔頂部加上 `import pytest`。

**Step 2: 確認測試失敗**

```bash
cd backend && ./venv/bin/pytest tests/api/test_fundamental_api_fields.py -v
```

**Step 3: 更新 `api.py`**

① `AnalyzeResponse` 新增欄位：
```python
fundamental_data: dict[str, Any] | None = None
```

② `initial_state` 補欄位：
```python
"fundamental_data": None,
"fundamental_context": None,
```

③ `return AnalyzeResponse(...)` 新增：
```python
fundamental_data=result.get("fundamental_data"),
```

④ `data_sources` 區塊新增：
```python
_fund = result.get("fundamental_data")
if _fund and not _fund.get("error"):
    _sources.append("finmind-fundamental")
```

**Step 4: 更新 `main.py`**

在 CLI 的 `initial_state` 中補齊：
```python
"fundamental_data": None,
"fundamental_context": None,
```

**Step 5: 確認測試通過**

```bash
cd backend && ./venv/bin/pytest tests/api/test_fundamental_api_fields.py -v
```

**Step 6: 跑全套測試**

```bash
cd backend && ./venv/bin/pytest --tb=short -q
```
Expected: all passed

**Step 7: Commit**

```bash
git add src/ai_stock_sentinel/api.py src/ai_stock_sentinel/main.py tests/api/test_fundamental_api_fields.py
git commit -m "feat: expose fundamental_data in AnalyzeResponse"
```

---

## Task 8：前端顯示基本面小卡

**Files:**
- Modify: `frontend/src/App.tsx`（或對應元件）

> 注意：請先閱讀現有前端 `App.tsx` 的結構，確認 `analysis_detail` 三維小卡的實作位置，再在同一區塊之後新增基本面小卡。

**Step 1: 閱讀現有前端結構**

確認 `fundamental_insight`（來自 `analysis_detail.fundamental_insight`）和 `fundamental_data`（頂層欄位）的渲染位置。

**Step 2: 新增 TypeScript 型別**

在 `AnalyzeResponse` interface 中新增：
```ts
fundamental_data?: {
  ttm_eps?: number | null
  pe_current?: number | null
  pe_band?: string | null
  pe_percentile?: number | null
  dividend_yield?: number | null
  yield_signal?: string | null
}
```

在 `AnalysisDetail` interface 中新增：
```ts
fundamental_insight?: string | null
```

**Step 3: 新增基本面小卡**

在三維分析小卡（技術面/籌碼面/消息面）之後，新增基本面小卡：

```tsx
{/* 基本面估值小卡 */}
<div className="bg-white rounded-xl shadow p-4">
  <h3 className="font-semibold text-gray-700 mb-2">📊 基本面估值</h3>
  {result.analysis_detail?.fundamental_insight ? (
    <InsightText text={result.analysis_detail.fundamental_insight} />
  ) : result.fundamental_data ? (
    <div className="text-sm text-gray-600 space-y-1">
      {result.fundamental_data.pe_current != null && (
        <p>PE：{result.fundamental_data.pe_current.toFixed(1)} 倍（
          {result.fundamental_data.pe_band === 'cheap' ? '偏低' :
           result.fundamental_data.pe_band === 'expensive' ? '偏高' : '合理'}
        ）</p>
      )}
      {result.fundamental_data.dividend_yield != null && (
        <p>殖利率：{result.fundamental_data.dividend_yield.toFixed(2)}%</p>
      )}
      {result.fundamental_data.pe_percentile != null && (
        <p>PE 百分位：{result.fundamental_data.pe_percentile.toFixed(0)}%</p>
      )}
    </div>
  ) : (
    <p className="text-sm text-gray-400">本次無基本面資料</p>
  )}
</div>
```

**Step 4: 目視確認**

啟動前端（`cd frontend && pnpm dev`）並用 2330.TW 測試，確認基本面小卡正確顯示或顯示「本次無基本面資料」（FinMind 無 API key 時）。

**Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: add fundamental valuation card to frontend"
```

---

## Task 9：進度文件更新

**Files:**
- Modify: `docs/progress-tracker.md`

在 `progress-tracker.md` 的「進行中 / 待完成」區塊，將第 6 項「基本面 / 估值工具」的子項目標記完成，並新增完成日期與測試數量。

**Commit:**

```bash
git add docs/progress-tracker.md
git commit -m "docs: mark fundamental valuation tasks complete in progress tracker"
```

---

## 驗收標準（Definition of Done）

- [ ] `./venv/bin/pytest --tb=short -q` 全部通過（比實作前多 12+ tests）
- [ ] `FundamentalData` dataclass 含 PE Band / 殖利率 / TTM EPS 欄位
- [ ] `AnalysisDetail.fundamental_insight` 欄位存在且 None-safe
- [ ] `AnalyzeResponse.fundamental_data` 欄位回傳
- [ ] LLM Prompt 含 `【基本面估值】` 段落
- [ ] 前端基本面小卡：有資料時顯示 PE/殖利率，無資料時顯示灰色提示
- [ ] FinMind API key 缺失時不拋例外，`fundamental_data` 含 `error` 鍵，流程繼續
- [ ] `data_sources` 陣列在基本面資料成功時包含 `"finmind-fundamental"`
