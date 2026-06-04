# Single Trade Review Analysis Implementation Plan

> Status: Draft for discussion
> Date: 2026-06-04
> Scope: Review one closed trade at a time using price/volume technical evidence. No aggregate user behavior analysis in MVP.

## Goal

Add a per-trade review feature for closed portfolio records. The feature helps the user inspect one completed buy/sell operation and identify whether the entry, holding path, and exit were technically reasonable.

The first version should be deterministic and rule-based. It should use market price/volume data and computed indicators as the primary evidence. LLM output is optional and should only summarize already-computed findings, not decide technical facts.

## Product Positioning

This is a third analysis perspective, separate from existing analysis flows:

| Perspective | Existing Route / Page | Core Question |
| --- | --- | --- |
| New position analysis | `POST /analyze`, `/analyze` | Is this stock worth watching or entering now? |
| Active position diagnosis | `POST /analyze/position`, `/portfolio` | For a currently held position, should I hold, trim, or exit? |
| Single trade review | New review endpoint, `/portfolio/closed` | For this completed trade, what did the operation do well or poorly? |

The review must not become an all-trades dashboard in the first version. Every review is scoped to exactly one closed `UserPortfolio` row.

## Non-Goals

- Do not analyze the user's overall trading behavior across all trades.
- Do not compute global mistake statistics or leaderboard-style summaries.
- Do not depend on `DailyAnalysisLog` being present for every holding day.
- Do not infer that the user ignored system advice unless such advice was actually recorded and explicitly included in a later version.
- Do not ask the LLM to inspect raw K-line arrays and invent conclusions.
- Do not make LLM required for MVP.

## Existing Building Blocks

Relevant current code:

- `backend/src/ai_stock_sentinel/db/models.py`
  - `UserPortfolio` already stores closed trade fields: `entry_price`, `entry_date`, `exit_price`, `exit_date`, `exit_quantity`, `exit_fees`, `exit_taxes`, `realized_pnl`, `realized_return_pct`, `holding_days`.
  - `StockRawData` stores raw market snapshots by symbol/date.
  - `DailyAnalysisLog` exists but should not be the primary evidence source for MVP.
- `backend/src/ai_stock_sentinel/portfolio/router.py`
  - `POST /portfolio/{portfolio_id}/close` computes realized PnL and creates inactive closed rows for full or partial exits.
  - `GET /portfolio/closed` lists closed records.
- `backend/src/ai_stock_sentinel/analysis/metrics.py`
  - Existing indicator implementations: MA, RSI, Bollinger, MACD, KD, ADX, OBV, ATR, MFI, Donchian.
- `frontend/src/pages/ClosedPortfolioPage.tsx`
  - Existing placement for closed trade list and realized PnL display.
- `frontend/src/lib/portfolioTypes.ts`
  - Existing `ClosedPortfolioItem` type.

## Core Design Decision

Use technical price/volume evidence as the review source of truth.

The review engine should fetch or reconstruct the historical price path between `entry_date` and `exit_date`, compute indicators locally, classify entry/exit behavior with deterministic rules, and return a structured review object.

`DailyAnalysisLog` can be added later as optional context, but it should not block or define the MVP because users may not run analysis every day.

## Proposed User Flow

1. User opens `/portfolio/closed`.
2. Each closed trade row has a `檢討分析` action.
3. User clicks the action for one trade.
4. Frontend calls `POST /portfolio/{portfolio_id}/review`.
5. Backend validates that the portfolio row belongs to the user and is closed.
6. Backend loads historical OHLCV data from entry date to exit date.
7. Backend computes deterministic review metrics and tags.
8. Frontend displays a review modal or expanded panel for that trade.

## Proposed Backend API

### `POST /portfolio/{portfolio_id}/review`

Generate or refresh a review for one closed portfolio row.

Initial behavior can be stateless and compute on demand. Persistence can be added after the review schema stabilizes.

Request body for MVP:

```json
{}
```

Possible future request fields:

```json
{
  "use_llm_summary": false,
  "refresh": false
}
```

Response shape:

```json
{
  "portfolio_id": 123,
  "symbol": "2330.TW",
  "data_quality": {
    "price_data_available": true,
    "trading_days": 28,
    "missing_days": 0,
    "notes": []
  },
  "trade_result": {
    "entry_date": "2026-01-05",
    "exit_date": "2026-02-14",
    "entry_price": 980.0,
    "exit_price": 1040.0,
    "holding_days": 40,
    "realized_pnl": 60000.0,
    "realized_return_pct": 6.12,
    "max_profit_pct": 12.4,
    "max_drawdown_pct": -4.8,
    "max_runup_price": 1102.0,
    "max_drawdown_price": 933.0
  },
  "entry_review": {
    "classification": "breakout_entry",
    "summary": "進場位於短期均線上方，屬突破後進場。",
    "signals": ["price_above_ma20", "volume_expansion"],
    "warnings": []
  },
  "holding_review": {
    "summary": "持有期間曾有明顯浮盈，回撤未跌破主要防守線。",
    "risk_events": [
      {
        "date": "2026-01-28",
        "type": "ma20_break_test",
        "description": "盤中接近 MA20，但收盤未有效跌破。"
      }
    ]
  },
  "exit_review": {
    "classification": "profit_protection_exit",
    "summary": "出場仍保留獲利，但相對最高價有回吐。",
    "signals": ["below_recent_high", "macd_cooling"],
    "warnings": ["gave_back_profit"]
  },
  "operation_review": {
    "primary_issue": "exit_timing",
    "mistake_tags": ["gave_back_profit"],
    "good_behavior_tags": ["profitable_exit", "held_above_support"],
    "next_time_rules": [
      "已有 10% 以上浮盈且 MACD 動能轉弱時，至少先設定移動停利。",
      "若跌破 MA20 且量能放大，下次不等待基本面敘事來合理化持有。"
    ]
  },
  "llm_summary": null
}
```

### `GET /portfolio/{portfolio_id}/review`

Optional for a later persistence phase. Returns the latest stored review if a `trade_review` table is introduced.

## Review Engine Modules

Suggested new backend module:

```text
backend/src/ai_stock_sentinel/analysis/trade_review.py
```

Initial functions:

```python
def build_trade_price_frame(symbol: str, entry_date: date, exit_date: date) -> pd.DataFrame:
    """Load OHLCV history for the trade window."""

def compute_trade_path_metrics(portfolio: UserPortfolio, prices: pd.DataFrame) -> dict:
    """Compute max profit, max drawdown, runup, drawdown, and holding path facts."""

def classify_entry(entry_price: float, entry_row: pd.Series, indicators: dict) -> dict:
    """Classify entry behavior using deterministic technical rules."""

def classify_holding_path(prices: pd.DataFrame, indicators: dict) -> dict:
    """Detect risk events during the holding window."""

def classify_exit(exit_price: float, exit_row: pd.Series, indicators: dict) -> dict:
    """Classify exit behavior using deterministic technical rules."""

def build_trade_review(portfolio: UserPortfolio, prices: pd.DataFrame) -> dict:
    """Compose the final structured review response."""
```

## Technical Rule Categories

The first version should keep rules explainable and testable.

### Entry Classifications

Possible `entry_review.classification` values:

- `breakout_entry`: entry near a breakout with volume support.
- `pullback_entry`: entry near MA20/MA60 or prior support after pullback.
- `chase_entry`: entry far above MA20/MA60 or near upper Bollinger with hot RSI/KD.
- `weak_entry`: entry while price is below key moving averages or trend is bearish.
- `range_entry`: entry inside sideways range without clear breakout.
- `insufficient_data`: not enough history to classify.

Example rules:

- `chase_entry` if entry price is more than X% above MA20 and Bollinger position is near upper band.
- `pullback_entry` if entry price is near MA20/MA60 and no major breakdown signal exists.
- `breakout_entry` if entry day closes above recent high with above-average volume.

### Holding Path Events

Possible event types:

- `ma20_break`
- `ma60_break`
- `support_break`
- `volume_down_day`
- `macd_bearish_turn`
- `kd_bearish_cross`
- `rsi_overheated`
- `bollinger_upper_rejection`
- `profit_giveback`
- `new_high_continuation`

### Exit Classifications

Possible `exit_review.classification` values:

- `profit_protection_exit`: exit protected realized gains after momentum cooled.
- `stop_loss_exit`: exit limited loss after breakdown.
- `late_stop_exit`: exit happened after a large drawdown or delayed breakdown response.
- `early_profit_exit`: exit captured small profit before trend continuation.
- `panic_exit`: exit near short-term low after sharp drop without confirming breakdown.
- `technical_break_exit`: exit aligned with support or moving-average breakdown.
- `insufficient_data`: not enough history to classify.

## Metrics To Compute

Minimum MVP metrics:

- `realized_return_pct`
- `holding_days`
- `max_profit_pct`
- `max_drawdown_pct`
- `profit_giveback_pct`
- `entry_vs_ma20_pct`
- `entry_vs_ma60_pct`
- `exit_vs_ma20_pct`
- `exit_vs_ma60_pct`
- `entry_volume_ratio`
- `exit_volume_ratio`
- `highest_close_during_holding`
- `lowest_close_during_holding`

Optional later metrics:

- ATR-based risk multiple
- MAE/MFE in R-multiple terms if user records planned stop
- Relative return vs market index
- Gap event detection

## Data Quality Rules

The response must be honest when price data is incomplete.

Examples:

- If fewer than 20 trading days of pre-entry history exist, MA20-based classification may be unavailable.
- If OHLCV data is missing around entry or exit date, return `insufficient_data` for affected sections.
- If the trade window is very short, holding path review should say the holding period is too short for trend evaluation.

No review should claim a signal occurred if the data was not available.

## LLM Policy

LLM is optional and should not be part of the MVP critical path.

Allowed LLM usage:

- Convert structured rule results into a concise natural-language report.
- Explain `mistake_tags` and `good_behavior_tags` in user-friendly language.
- Generate `next_time_rules` from already-computed tags and metrics.

Forbidden LLM usage:

- Calculate profit/loss, drawdown, indicators, or moving averages.
- Decide whether price crossed a technical level without a precomputed boolean from Python.
- Invent missing market events.
- Compare against daily system diagnosis records unless explicitly provided.

Recommended architecture:

```text
OHLCV history
  -> technical indicators
  -> deterministic classifications
  -> structured review JSON
  -> optional LLM narrative
```

## Persistence Decision

MVP can compute on demand and avoid a new table while the review schema is still being discussed.

Add persistence only after the response shape stabilizes.

Future table candidate:

```text
trade_review
  id
  user_id
  portfolio_id
  symbol
  review_version
  review_result JSONB
  llm_summary TEXT NULL
  created_at
  updated_at
```

Reasons to persist later:

- Avoid recalculating expensive historical data.
- Preserve the review generated under a specific rule version.
- Support future manual notes or user feedback.

Reasons not to persist in MVP:

- Schema is still likely to change.
- The first version should prioritize validating usefulness of the rule output.

## Frontend UX

Target file:

```text
frontend/src/pages/ClosedPortfolioPage.tsx
```

MVP UI proposal:

- Add `檢討分析` button to each closed trade row.
- Open a modal or expandable panel.
- Show loading state while the review is generated.
- Show data quality warning first if price data is incomplete.
- Split the review into four sections:
  - `交易結果`
  - `進場檢討`
  - `持有路徑`
  - `出場檢討`
  - `下次規則`

Keep the page single-trade focused. Do not add aggregate charts in this task.

## Testing Plan

Backend tests:

- `trade_review.py` unit tests for metrics and classifications.
- API tests for closed-only authorization and response shape.
- Data quality tests for insufficient price history.
- Partial close tests to ensure review uses the closed slice, not the remaining active row.

Frontend tests or manual checks:

- Closed trade row shows review button.
- Review modal loads and renders all sections.
- Error state appears when backend returns insufficient data or HTTP error.
- Existing period filter and realized PnL summary remain unchanged.

Suggested commands after implementation:

```bash
cd backend && pytest tests/test_trade_review.py tests/test_portfolio_router.py -v
cd frontend && pnpm build
```

## Open Discussion Questions

1. Should MVP compute review on demand only, or create `trade_review` persistence immediately?
2. Which classifications should be shown to the user in Chinese labels first?
3. Should the first version include optional `llm_summary`, or keep it fully rule-based?
4. Should entry/exit analysis use execution price directly, or nearest trading day's close when dates fall on non-trading days?
5. Should the review compare against the user's `notes` field if notes contain an entry thesis?

## Suggested MVP Cut

The smallest useful version:

1. Add backend rule engine for one closed trade.
2. Add `POST /portfolio/{id}/review` without persistence.
3. Return structured JSON with data quality, trade result, entry review, holding review, exit review, and operation review.
4. Add a closed-row review modal in `/portfolio/closed`.
5. Do not use LLM in the first implementation.
