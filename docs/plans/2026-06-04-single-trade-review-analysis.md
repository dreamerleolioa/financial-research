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

Two safeguards are part of the MVP design, not future enhancements:

1. Point-in-time review: each review stage must only use information that would have been available at that stage.
2. Market-regime-aware rules: indicator rules must be interpreted differently in trend, range, strong momentum, weak downtrend, and high-volatility regimes.

## Proposed User Flow

1. User opens `/portfolio/closed`.
2. Each closed trade row has a `檢討分析` action.
3. User clicks the action for one trade.
4. Frontend calls `POST /portfolio/{portfolio_id}/review`.
5. Backend validates that the portfolio row belongs to the user and is closed.
6. Backend returns an existing saved review if one already exists.
7. If no saved review exists, backend loads historical OHLCV data from entry date to exit date.
8. Backend computes deterministic review metrics, tags, and an evidence payload.
9. Backend saves the review and evidence payload to `trade_review`.
10. Frontend displays a review modal or expanded panel for that trade, including a copyable indicator/evidence block.

## Proposed Backend API

### `POST /portfolio/{portfolio_id}/review`

Generate the first review for one closed portfolio row, or return the existing saved review.

MVP behavior is persisted by default. A closed trade should normally be reviewed once, then reopened from the saved `trade_review` row. Re-analysis can be added later as an explicit action, but should not happen silently on every click.

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

`refresh` is intentionally future-only. If added, it should create or update a review with a new `review_version`, not silently mutate historical output without version context.

Response shape:

```json
{
  "review_id": 456,
  "portfolio_id": 123,
  "symbol": "2330.TW",
  "review_version": "trade-review-v1",
  "created_at": "2026-06-04T10:30:00Z",
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
  "evidence_payload": {
    "trade": {
      "symbol": "2330.TW",
      "entry_date": "2026-01-05",
      "entry_price": 980.0,
      "exit_date": "2026-02-14",
      "exit_price": 1040.0,
      "return_pct": 6.12,
      "holding_days": 40
    },
    "path_metrics": {
      "max_profit_pct": 12.4,
      "max_drawdown_pct": -4.8,
      "profit_giveback_pct": 6.2
    },
    "entry_indicators": {
      "ma20": 950.0,
      "ma60": 910.0,
      "rsi14": 72.0,
      "macd_bias": "bullish",
      "volume_ratio": 1.8,
      "market_regime": "strong_momentum"
    },
    "exit_indicators": {
      "ma20": 1055.0,
      "ma60": 990.0,
      "rsi14": 48.0,
      "macd_bias": "bearish",
      "volume_ratio": 1.3,
      "market_regime": "uptrend"
    },
    "detected_events": [
      {
        "date": "2026-02-07",
        "type": "macd_bearish_turn"
      },
      {
        "date": "2026-02-10",
        "type": "profit_giveback"
      }
    ]
  },
  "llm_summary": null
}
```

### `GET /portfolio/{portfolio_id}/review`

Return the saved review for one closed portfolio row.

If no review exists yet, return `404` or a structured empty state so the frontend can show `尚未產生檢討分析` and offer the generate action.

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

Before applying entry, holding, or exit classifications, compute a lightweight `market_regime` so rules do not treat every market condition the same way.

Possible `market_regime` values:

- `uptrend`: price structure and moving averages show a sustained upward trend.
- `downtrend`: price structure and moving averages show sustained weakness.
- `range_bound`: price oscillates within a range and moving-average signals are less reliable.
- `strong_momentum`: trend is extended and overbought indicators may remain elevated.
- `high_volatility`: ATR or wide daily ranges make simple MA breaks noisier.
- `insufficient_data`: not enough history to classify regime.

Example regime adjustments:

- In `uptrend`, MA20 breaks and MACD cooling are more meaningful for exit review.
- In `range_bound`, MA20 crosses should be treated as lower-confidence noise unless the range boundary also breaks.
- In `strong_momentum`, RSI/KD overbought alone should not be labeled as a sell mistake; require price/volume divergence, MA5/MA10 loss, or failed breakout evidence.
- In `downtrend`, rebound entries should be reviewed more strictly and require stronger confirmation.
- In `high_volatility`, ATR-adjusted thresholds should reduce false breakdown classifications.

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

## Bias Control: Point-in-Time Review

The review must avoid hindsight bias. The system may report full-trade outcome metrics, but it must not use future candles to judge what the user should have known at entry or exit.

Stage-specific data windows:

| Review Stage | Allowed Data Window | Forbidden Use |
| --- | --- | --- |
| Entry review | Data available up to and including `entry_date` | Do not use post-entry highs/lows to say the entry was obviously good or bad. |
| Holding review | Data from after entry up to before/at exit, evaluated chronologically | Do not assume the final exit result was knowable during earlier holding days. |
| Exit review | Data available up to and including `exit_date` | Do not use post-exit price movement unless a future version explicitly adds a separate missed-opportunity section. |
| Trade result summary | Full trade window from entry through exit | May report max profit, max drawdown, and profit giveback, but only as outcome facts. |

Examples of acceptable wording:

- "Entry was technically extended based on information available at entry."
- "The trade later reached a larger unrealized profit, but this is reported as an outcome metric, not as proof that the entry decision was wrong."
- "Exit occurred after MA20 weakness had already appeared by the exit date."

Examples of forbidden wording:

- "You should have known at entry that the stock would later reverse."
- "The correct exit was the exact high of the trade window."
- "This was a mistake because the stock rose after exit" unless post-exit analysis is explicitly enabled as a different feature.

Implementation guidance:

- Indicator snapshots for entry classification must be computed on a sliced price frame ending at `entry_date`.
- Exit classification must be computed on a sliced price frame ending at `exit_date`.
- Holding path events should be detected in chronological order and returned with event dates.
- Full-window max profit/drawdown should live under `trade_result`, not under entry or exit judgment.

## Rule Confidence and Caveats

Every classification should carry enough context for the frontend to avoid presenting it as an absolute verdict.

Suggested fields:

```json
{
  "classification": "chase_entry",
  "confidence": "medium",
  "market_regime": "strong_momentum",
  "caveats": [
    "RSI was elevated, but strong momentum regimes can remain overbought longer than expected."
  ]
}
```

Suggested confidence levels:

- `high`: multiple aligned signals support the classification and data quality is sufficient.
- `medium`: classification is supported, but market regime or missing secondary signals reduce certainty.
- `low`: limited data, conflicting indicators, or noisy regime.

Frontend copy should prefer measured language such as "偏向", "可能", "需注意" for low/medium confidence results.

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

MVP should persist the review result.

Reason: this feature is a completed-trade retrospective, not live market analysis. A user usually expects the same closed trade review to remain stable when reopened. The generated review should also preserve the exact indicator/evidence payload used at the time, so the user can copy it into another AI agent or external tool.

Add a new table:

```text
trade_review
  id
  user_id
  portfolio_id
  symbol
  review_version
  review_result JSONB
  evidence_payload JSONB
  llm_summary TEXT NULL
  created_at
  updated_at
```

Recommended constraints and indexes:

- Unique: `(user_id, portfolio_id)` so one closed trade has one default review.
- Index: `symbol` for future lookup/debugging.
- Foreign key: `portfolio_id -> user_portfolio.id`.

Persistence behavior:

- `POST /portfolio/{portfolio_id}/review` creates the review when missing.
- If a review already exists, `POST` returns the saved result instead of recalculating.
- `GET /portfolio/{portfolio_id}/review` returns the saved result.
- Future `refresh` behavior must be explicit and version-aware.
- Save must be atomic: `review_result` and `evidence_payload` are inserted in the same DB transaction. If any step fails, rollback and leave no partial `trade_review` row.

Reasons to persist in MVP:

- Avoid recalculating expensive historical data.
- Preserve the review generated under a specific rule version.
- Preserve the exact evidence payload used by the review.
- Let users repeatedly reopen or copy the same review without drift.

Schema flexibility:

- Keep detailed output in `review_result` and `evidence_payload` JSONB so the first version can evolve without frequent column migrations.
- Store `review_version` as a string constant such as `trade-review-v1` to distinguish old reviews from future rule updates.
- Do not store full OHLCV/K-line arrays in `trade_review`. Store only summarized metrics, point-in-time indicators, detected events, and data quality notes.
- If a future version needs full historical candles, store them in a dedicated raw-data table or reuse `StockRawData`; do not duplicate them inside every review row.

Atomic save requirements:

- Build `review_result` and `evidence_payload` fully in memory first.
- Validate both payloads contain required top-level keys before DB insert.
- Insert `trade_review` only after both payloads are complete.
- Commit once after insert succeeds.
- Roll back on any exception and return an error response; do not persist partial analysis.

## Evidence Package

The review must include a copyable `evidence_payload` separate from the user-facing review text.

Purpose:

- Make the analysis transparent and inspectable.
- Let the user copy objective trade metrics and indicators into another AI agent.
- Avoid black-box conclusions; every review should be traceable to concrete values.

Minimum evidence sections:

- `trade`: symbol, entry/exit dates, entry/exit prices, return, holding days.
- `path_metrics`: max profit, max drawdown, profit giveback, highest/lowest close during holding.
- `entry_indicators`: point-in-time indicators computed using data up to `entry_date`.
- `exit_indicators`: point-in-time indicators computed using data up to `exit_date`.
- `market_regime`: regime labels used for entry/exit classification.
- `detected_events`: chronological holding-period events with dates and event types.
- `data_quality`: missing data notes and confidence limitations.

Storage boundary:

- Include indicator values and derived events only.
- Do not include full daily OHLCV arrays.
- Do not include raw news, raw LLM prompts, or unrelated portfolio history.
- Keep event lists concise and capped if needed, for example top 20 chronologically important events.
- Prefer scalar values, labels, and short descriptions so the payload remains easy to copy into other AI agents.

Frontend should provide a `複製指標資料` action that copies a pretty-printed JSON payload.

## Frontend UX

Target file:

```text
frontend/src/pages/ClosedPortfolioPage.tsx
```

MVP UI proposal:

- Add `檢討分析` button to each closed trade row.
- Open a modal or expandable panel.
- Show loading state only when the review is generated for the first time.
- Show data quality warning first if price data is incomplete.
- Split the review into four sections:
  - `交易結果`
  - `進場檢討`
  - `持有路徑`
  - `出場檢討`
  - `下次規則`
- Add `複製指標資料` button for `evidence_payload`.
- If a saved review exists, open it directly without recomputation.

Keep the page single-trade focused. Do not add aggregate charts in this task.

## Testing Plan

Backend tests:

- `trade_review.py` unit tests for metrics and classifications.
- API tests for closed-only authorization and response shape.
- Persistence tests: first request creates `trade_review`, second request returns saved result without recomputing.
- Evidence tests: response includes copyable `evidence_payload` with trade, path metrics, entry indicators, exit indicators, and detected events.
- Storage boundary tests: `evidence_payload` does not include full OHLCV arrays or raw K-line series.
- Transaction tests: if evidence or review generation fails, no partial `trade_review` row is committed.
- Data quality tests for insufficient price history.
- Partial close tests to ensure review uses the closed slice, not the remaining active row.

Frontend tests or manual checks:

- Closed trade row shows review button.
- Review modal loads and renders all sections.
- Copy evidence button copies pretty-printed JSON.
- Error state appears when backend returns insufficient data or HTTP error.
- Existing period filter and realized PnL summary remain unchanged.

Suggested commands after implementation:

```bash
cd backend && pytest tests/test_trade_review.py tests/test_portfolio_router.py -v
cd frontend && pnpm build
```

## Open Discussion Questions

1. Which classifications should be shown to the user in Chinese labels first?
2. Should the first version include optional `llm_summary`, or keep it fully rule-based?
3. Should entry/exit analysis use execution price directly, or nearest trading day's close when dates fall on non-trading days?
4. Should the review compare against the user's `notes` field if notes contain an entry thesis?
5. Should a future `refresh` create a new historical review version or overwrite the existing row with an updated `review_version`?

## Suggested MVP Cut

The smallest useful version:

1. Add backend rule engine for one closed trade.
2. Add `trade_review` table and migration.
3. Add `POST /portfolio/{id}/review` to create-or-return one saved review.
4. Add `GET /portfolio/{id}/review` to read the saved review.
5. Return structured JSON with data quality, trade result, entry review, holding review, exit review, operation review, and `evidence_payload`.
6. Add a closed-row review modal in `/portfolio/closed`.
7. Add `複製指標資料` for the evidence payload.
8. Do not use LLM in the first implementation.
