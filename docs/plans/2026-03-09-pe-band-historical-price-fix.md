# PE Band 歷史股價修正 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修正 `FinMindFundamentalProvider` 的歷史 PE Band 計算邏輯——改用各季末真實歷史股價（從 yfinance 抓取）除以當季 TTM EPS，取代錯誤的「用今日股價除以歷史 EPS」方式，使 `pe_mean`、`pe_std`、`pe_percentile` 具有真實語義。

**Architecture:** 在 `FinMindFundamentalProvider.fetch()` 內新增 `_fetch_historical_prices(stock_id, quarter_dates)` 方法，透過 yfinance 抓取各季末收盤價；計算歷史 PE 時改用「該季末股價 / 該季 TTM EPS」；`pe_percentile` 語義修正為「當前 PE 在真實歷史 PE 分佈中的百分位」。`interface.py` 的 `FundamentalData` 欄位定義不變，只更新 docstring 說明。

**Tech Stack:** Python 3.10+, yfinance, statistics（標準庫）, pytest, unittest.mock

---

## 背景知識

### 現有問題（Bug）

`finmind_provider.py` 第 85-88 行：

```python
for i in range(4, len(eps_values) + 1):
    window_eps = sum(eps_values[i - 4:i])
    if window_eps and window_eps > 0:
        historical_pes.append(current_price / window_eps)  # ← 用今日股價！
```

`current_price`（今日股價）被重複用於所有歷史窗口。
正確做法：每個歷史窗口應用**該窗口最後一季的季末收盤價**。

### 修正後邏輯

```
各季末日期 = eps_rows 每筆的 date 欄位（例：2024-12-31）
各季末股價 = yfinance.history(period="max")["Close"] 在對應日期的值（或最近可用交易日）
historical_pe[i] = 季末股價[i] / ttm_eps[i]
pe_percentile   = 當前 PE 在 historical_pes 中的百分位（語義不變，但基準數值修正）
```

### yfinance 取季末股價

```python
import yfinance as yf

ticker = yf.Ticker("2330.TW")
hist = ticker.history(start="2019-01-01", end="2025-12-31", interval="1d")
# hist.index 為 DatetimeIndex，以最接近的交易日收盤價為準
```

取特定日期的價格：
```python
from datetime import date, timedelta

def _get_price_on_or_before(hist_df, target_date: date) -> float | None:
    # 找 target_date 當天或之前最近的收盤價
    candidates = hist_df[hist_df.index.date <= target_date]
    if candidates.empty:
        return None
    return float(candidates["Close"].iloc[-1])
```

---

## Task 1：修正 `FinMindFundamentalProvider` 歷史 PE 計算

**Files:**
- Modify: `backend/src/ai_stock_sentinel/data_sources/fundamental/finmind_provider.py`
- Modify: `backend/tests/test_finmind_fundamental_provider.py`

### Step 1：寫新的失敗測試

在 `tests/test_finmind_fundamental_provider.py` 末尾新增以下測試（**不修改現有測試**）：

```python
@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_historical_prices")
@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_pe_band_uses_historical_prices(mock_fetch_dataset, mock_fetch_prices):
    """歷史 PE 必須使用各季末真實股價，而非今日股價"""
    # 8 季 EPS，每季 10 元
    rows = [
        {"date": "2022-03-31", "type": "EPS", "value": 10.0},
        {"date": "2022-06-30", "type": "EPS", "value": 10.0},
        {"date": "2022-09-30", "type": "EPS", "value": 10.0},
        {"date": "2022-12-31", "type": "EPS", "value": 10.0},
        {"date": "2023-03-31", "type": "EPS", "value": 10.0},
        {"date": "2023-06-30", "type": "EPS", "value": 10.0},
        {"date": "2023-09-30", "type": "EPS", "value": 10.0},
        {"date": "2023-12-31", "type": "EPS", "value": 10.0},
    ]
    # 各季末股價（與今日股價 1000.0 完全不同）
    mock_fetch_prices.return_value = {
        "2022-03-31": 400.0,
        "2022-06-30": 420.0,
        "2022-09-30": 440.0,
        "2022-12-31": 460.0,
        "2023-03-31": 480.0,
        "2023-06-30": 500.0,
        "2023-09-30": 520.0,
        "2023-12-31": 540.0,
    }

    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return rows
        return []
    mock_fetch_dataset.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=1000.0)

    # TTM EPS = 40.0（最近4季合計）
    assert result.ttm_eps == pytest.approx(40.0, abs=0.01)
    # pe_current 用今日股價
    assert result.pe_current == pytest.approx(1000.0 / 40.0, abs=0.1)
    # pe_mean 應反映歷史股價，遠低於 1000/40=25
    # 歷史各窗口 pe：400/40=10, 420/40=10.5, 440/40=11, 460/40=11.5,
    #               480/40=12, 500/40=12.5, 520/40=13, 540/40=13.5
    # (共8窗口，mean ≈ 11.75)
    assert result.pe_mean is not None
    assert result.pe_mean == pytest.approx(11.75, abs=0.5)
    # pe_current=25 遠高於歷史均值，應為 expensive
    assert result.pe_band == "expensive"
    # pe_percentile：25 高於所有歷史 PE → 100%
    assert result.pe_percentile == pytest.approx(100.0, abs=1.0)


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_historical_prices")
@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_pe_band_falls_back_when_no_historical_prices(mock_fetch_dataset, mock_fetch_prices):
    """歷史股價取得失敗時，pe_band 應為 unknown，流程不中斷"""
    rows = [
        {"date": "2022-03-31", "type": "EPS", "value": 10.0},
        {"date": "2022-06-30", "type": "EPS", "value": 10.0},
        {"date": "2022-09-30", "type": "EPS", "value": 10.0},
        {"date": "2022-12-31", "type": "EPS", "value": 10.0},
        {"date": "2023-03-31", "type": "EPS", "value": 10.0},
        {"date": "2023-06-30", "type": "EPS", "value": 10.0},
    ]
    mock_fetch_prices.return_value = {}  # 無歷史股價

    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return rows
        return []
    mock_fetch_dataset.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=500.0)

    # pe_current 仍正常計算
    assert result.pe_current is not None
    # pe_band 無法計算歷史分佈，應為 unknown
    assert result.pe_band == "unknown"
    assert result.pe_mean is None
    assert result.pe_std is None
    assert result.pe_percentile is None
    assert any("歷史股價" in w for w in result.warnings)
```

### Step 2：確認測試失敗

```bash
cd backend && PYTHONPATH=src ./venv/bin/pytest tests/test_finmind_fundamental_provider.py::test_pe_band_uses_historical_prices tests/test_finmind_fundamental_provider.py::test_pe_band_falls_back_when_no_historical_prices -v
```

Expected: `AttributeError: _fetch_historical_prices`（方法不存在）

### Step 3：實作修正

將 `backend/src/ai_stock_sentinel/data_sources/fundamental/finmind_provider.py` 全部改寫為：

```python
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

    def _fetch_historical_prices(self, symbol: str, quarter_dates: list[str]) -> dict[str, float]:
        """
        從 yfinance 取得各季末收盤價。
        回傳 {date_str: price}，取不到的日期不包含在結果中。
        """
        if not quarter_dates:
            return {}
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 未安裝，無法取得歷史股價")
            return {}

        try:
            start = min(quarter_dates)
            # 多抓 10 天以涵蓋非交易日
            end_dt = date.fromisoformat(max(quarter_dates)) + timedelta(days=10)
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=start, end=end_dt.isoformat(), interval="1d")
            if hist.empty:
                return {}

            result: dict[str, float] = {}
            for date_str in quarter_dates:
                target = date.fromisoformat(date_str)
                # 找 target 當天或之前最近的收盤價
                candidates = hist[hist.index.date <= target]
                if not candidates.empty:
                    result[date_str] = float(candidates["Close"].iloc[-1])
            return result
        except Exception as exc:
            logger.warning("取得歷史股價失敗：%s", exc)
            return {}

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
        quarter_dates = [r.get("date", "") for r in eps_rows if _safe_float(r.get("value")) is not None]

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

                # 取歷史股價（各季末真實收盤價）
                historical_prices = self._fetch_historical_prices(symbol, quarter_dates)

                if not historical_prices:
                    warnings.append("無法取得歷史股價，PE Band 無法計算（使用 unknown）")
                else:
                    # 歷史 PE：逐季滑動（每 4 季一組），使用各窗口末季的真實股價
                    historical_pes: list[float] = []
                    for i in range(4, len(eps_values) + 1):
                        window_eps = sum(eps_values[i - 4:i])
                        if not window_eps or window_eps <= 0:
                            continue
                        # 取該窗口末季（index i-1）的季末股價
                        window_end_date = quarter_dates[i - 1]
                        hist_price = historical_prices.get(window_end_date)
                        if hist_price is None or hist_price <= 0:
                            continue
                        historical_pes.append(hist_price / window_eps)

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
                        warnings.append("有效歷史 PE 窗口不足 4 個，PE Band 無法計算")
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

### Step 4：確認新測試通過，舊測試不破

```bash
cd backend && PYTHONPATH=src ./venv/bin/pytest tests/test_finmind_fundamental_provider.py -v
```

Expected: 6 passed（原有 4 個 + 新增 2 個）

> **注意**：原有測試 `test_fetch_calculates_ttm_eps` 和 `test_pe_band_cheap` 沒有 mock `_fetch_historical_prices`，這在新版本中會讓它們自動呼叫真正的 `_fetch_historical_prices`（因為 `_fetch_dataset` 被 mock 但 `_fetch_historical_prices` 沒有）。需確認這兩個測試依然通過，或在它們也加上 mock。

若 `test_fetch_calculates_ttm_eps` 或 `test_pe_band_cheap` 因為 yfinance 呼叫失敗而行為改變，需在這兩個測試加上：

```python
@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_historical_prices")
```

並讓 `mock_fetch_prices.return_value = {}` 使其回傳空字典（觸發 fallback 路徑）。

### Step 5：跑全套測試確認無回歸

```bash
cd backend && PYTHONPATH=src ./venv/bin/pytest --tb=short -q
```

Expected: all passed

### Step 6：更新 `interface.py` docstring

修改 `interface.py` 中 `pe_mean` 和 `pe_percentile` 的注釋：

```python
pe_mean: float | None = None          # 歷史 PE 均值（各季末真實股價計算）
pe_std: float | None = None           # 歷史 PE 標準差（各季末真實股價計算）
pe_percentile: float | None = None    # 當前 PE 在歷史真實 PE 分佈的百分位（0-100）
```

### Step 7：Commit

```bash
cd backend && git add src/ai_stock_sentinel/data_sources/fundamental/finmind_provider.py src/ai_stock_sentinel/data_sources/fundamental/interface.py tests/test_finmind_fundamental_provider.py
git commit -m "fix: use real historical prices for PE Band calculation

Previously pe_mean/pe_std/pe_percentile used today's price divided by
historical EPS, making pe_mean semantically meaningless. Now fetches
actual quarter-end closing prices from yfinance for each TTM window."
```

---

## Task 2：更新架構規格文件與進度追蹤

**Files:**
- Modify: `docs/ai-stock-sentinel-architecture-spec.md`
- Modify: `docs/progress-tracker.md`

### Step 1：更新架構規格文件的 PE Band 定義

在 `docs/ai-stock-sentinel-architecture-spec.md` §3.1「資料源建議 > 基本面（估值）」，找到 `pe_band` 說明，更新為：

```
- `pe_band`：估值位階（`cheap` / `fair` / `expensive`），以近 20 季**各季末真實股價**計算的歷史 PE 均值 ± 1 標準差為邊界；無法取得歷史股價時回傳 `unknown`
- `pe_percentile`：當前 PE 在歷史**真實** PE 分佈的百分位（0–100）
```

同時在 §3.1「基本面工具」的失敗行為描述補充：

```
- 歷史股價（yfinance）取得失敗時：`pe_band = "unknown"`，`pe_mean = null`，`warnings` 記錄原因，流程繼續
```

### Step 2：在進度追蹤補上此修正

在 `docs/progress-tracker.md` 的「#### 6. 基本面 / 估值工具」區塊末尾新增：

```markdown
- [x] Task 10（Bug Fix）：PE Band 歷史股價修正——`_fetch_historical_prices()` 改用 yfinance 各季末真實收盤價計算歷史 PE，修正原本以今日股價計算歷史 PE 的邏輯錯誤；無歷史股價時 `pe_band = "unknown"` 並記錄 warning（2026-03-09）
```

### Step 3：Commit

```bash
git add docs/ai-stock-sentinel-architecture-spec.md docs/progress-tracker.md
git commit -m "docs: update PE Band spec to reflect historical price fix"
```

---

## 驗收標準

- [ ] `pytest --tb=short -q` 全部通過（比修正前多 2 tests）
- [ ] `pe_mean` 語義正確：反映歷史各季末股價除以當季 TTM EPS 的均值
- [ ] 歷史股價取得失敗時（yfinance 無連線）：`pe_band = "unknown"`、`pe_mean = None`、`warnings` 包含說明，流程不中斷
- [ ] `pe_current` 仍用今日股價計算（不受修正影響）
- [ ] 殖利率邏輯不受影響
