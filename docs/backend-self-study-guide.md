# AI Stock Sentinel 後端自學導覽

> 更新日期：2026-06-12
> 目的：讓未來的我可以快速理解目前後端用了哪些技術、怎麼分層、每個需求實際落在哪些模組，以及要從哪裡開始讀程式。
> 關係：本文件是學習導覽，不是 API contract。正式 API 欄位以 `docs/specs/backend-api-technical-spec.md` 為準，長期架構事實以 `docs/specs/ai-stock-sentinel-architecture-spec.md` 為準。

---

## 1. 先掌握這個後端在做什麼

AI Stock Sentinel 後端目前有四個主要產品表面：

| 表面 | 入口 | 解決的需求 |
| ---- | ---- | ---------- |
| 新倉分析 | `POST /analyze` | 輸入股票代碼，抓取行情、新聞、籌碼、基本面，輸出研究 setup、風險語言與可追溯分析 |
| 持股診斷 | `POST /analyze/position` | 已有成本價時，判斷持股風險、續抱/減碼/出場條件與防守線 |
| Portfolio lifecycle | `/portfolio/*` | 保存持股、加碼、出場、事件 ledger、進場脈絡、lifecycle plan、交易復盤 |
| Daily Radar | `/internal/daily-radar/*`, `GET /daily-radar/*` | 每日收盤後產生隔日觀察清單，保存 deterministic scoring trace、forward validation 與 monthly rule review |

共同原則：

- 所有數值與 rule-based 判斷由 Python 計算，LLM 不估算技術指標、不覆寫 deterministic action / ranking / verdict。
- LLM 主要負責自然語言分析與整理，但輸入已經包含 rule-based labels、technical evidence、risk context。
- `shared_background_contexts` 只作 evidence、caveat、data quality trace，不改 Daily Radar ranking、不改 portfolio action、不改 lifecycle verdict。

---

## 2. 技術棧

| 類別 | 技術 | 在本專案的角色 |
| ---- | ---- | -------------- |
| Web API | FastAPI | 對外與 internal endpoint，集中在 `api.py` app setup 與各 feature router |
| Orchestration | LangGraph | `/analyze` 與 `/analyze/position` 的多節點流程與 retry loop |
| LLM | LangChain + Anthropic / OpenAI fallback | `LangChainStockAnalyzer` 生成結構化分析與文字敘事 |
| DB ORM | SQLAlchemy 2 | models、query、transaction、repository |
| Migration | Alembic | DB schema 演進 |
| Data | yfinance、RSS、FinMind、TWSE RWD/OpenAPI、TPEX、TDCC | 行情、新聞、法人籌碼、基本面、背景 context |
| Testing | pytest + httpx | 單元、router、contract、migration、release gate |
| Package | uv | Python dependency 與 test/runtime command |

---

## 3. 後端目錄怎麼讀

建議讀法：

1. 先讀 API 組裝：`backend/src/ai_stock_sentinel/api.py`
2. 再讀 LangGraph：`graph/builder.py`、`graph/nodes.py`、`graph/state.py`
3. 再讀資料來源：`data_sources/`
4. 再讀 Daily Radar：`daily_radar/router.py`、`daily_radar/service.py`、`daily_radar/scoring.py`
5. 再讀 Phase 1 AVWAP：`phase1_avwap/service.py`、`phase1_avwap/projection.py`、`phase1_avwap/calculator.py`
6. 再讀 Portfolio lifecycle：`portfolio/router.py`、`analysis/position_lifecycle.py`、`analysis/trade_review.py`
7. 最後讀 DB model：`db/models.py`

| 路徑 | 責任 |
| ---- | ---- |
| `api.py` | FastAPI app setup、middleware、router include、health check、internal raw data endpoint |
| `main.py` | 建立 crawler、analyzer、RSS client、news cleaner，供 CLI 與 API dependency 使用 |
| `config.py` | logging、settings、`STRATEGY_VERSION` |
| `graph/` | LangGraph state machine、節點、GraphState |
| `analysis/` | `/analyze` router、schemas、application use cases、cache/response assembly、LLM analyzer、news cleaner、quality gate、confidence scorer、technical metrics、strategy generator、position scorer、trade/lifecycle review |
| `data_sources/` | yfinance、RSS、FinMind、institutional flow provider、fundamental provider |
| `daily_radar/` | universe、raw data backfill、prefilter、scoring、market context、relative strength、background context、forward validation、rule governance |
| `phase1_avwap/` | managed-universe resolver、FinMind `TaiwanStockPrice` daily provider、日頻 AVWAP calculation、snapshot repository/service、Analyze/Portfolio/Daily Radar read-only projections |
| `portfolio/` | portfolio CRUD、entry record contract、fees、risk summary、history router |
| `watchlist/` | watchlist schemas、repository、application use cases、CRUD/reorder router |
| `shared_context.py` | 讀取 shared background context 並轉成 evidence/caveat/data quality payload |
| `db/models.py` | 所有主要 DB table 的 SQLAlchemy model |

---

## 4. `/analyze` 是怎麼跑的

### 4.1 API 層流程

入口：`backend/src/ai_stock_sentinel/analysis/router.py`

核心流程：

1. 檢查 L1 analysis cache：`stock_analysis_cache`，`analysis_type="general"`。
2. 若 `skip_ai=true`，檢查 10 分鐘內 L2 raw cache：`stock_raw_data`。
3. 驗證 symbol 是否存在，避免無效標的進入 LLM。
4. 補昨天 context：`backfill_yesterday_indicators()`、`load_yesterday_context()`。
5. 建立 `GraphState`。
6. 呼叫 LangGraph：`graph.invoke(initial_state)`。
7. 組 response：`_build_response()`。
8. 寫回 `stock_analysis_cache`、`daily_analysis_log`、`stock_raw_data`。
9. 附加 shared context：`_with_shared_context(..., consumer="analyze")`。

### 4.2 LangGraph 節點

組裝位置：`graph/builder.py`

```text
crawl
  -> fetch_external_data
  -> judge
    -> clean
    -> fetch_news -> increment_retry -> crawl
    -> increment_retry -> crawl
  -> quality_gate
  -> preprocess
  -> score
  -> analyze
  -> strategy
```

| 節點 | 實作 | 責任 |
| ---- | ---- | ---- |
| `crawl` | `crawl_node` | 用 yfinance 抓股票快照 |
| `fetch_external_data` | `fetch_external_data_node` | 並行抓籌碼與基本面 |
| `judge` | `judge_node` | 判斷資料是否足夠、是否需要補新聞 |
| `fetch_news` | `fetch_news_node` | RSS 抓新聞 |
| `clean` | `clean_node` | 清潔與結構化新聞 |
| `quality_gate` | `quality_gate_node` | 評估新聞品質 |
| `preprocess` | `preprocess_node` | 產生技術面/基本面 context 與指標欄位 |
| `score` | `score_node` | 計算信心分數、data confidence、cross validation |
| `analyze` | `analyze_node` | 組 LLM signal summary 並呼叫 analyzer |
| `strategy` | `strategy_node` | 產生 strategy type、action plan、risk language trace |

### 4.3 LLM 的邊界

LLM 看到的是 `_build_llm_signal_summary()` 整理過的 packet，包含：

- rule-based labels：`technical_signal`、`institutional_flow`、`sentiment_label`、`confidence_score`
- technical evidence：MA、RSI、Bollinger、MACD、KD、ADX、OBV、ATR、MFI、Donchian、支撐壓力
- news evidence：sentiment counts / strength
- strategy evidence：`strategy_type`、`action_plan_tag`、`conviction_level`

LLM 不負責：

- 計算技術指標
- 計算信心分數
- 決定 final action
- 覆寫 `risk_state`、`action_plan_tag`、Daily Radar ranking 或 portfolio verdict

---

## 5. `/analyze/position` 是怎麼不同的

入口同樣在 `analysis/router.py`，但 `analysis_type="position"`，request 會帶：

- `symbol`
- `entry_price`
- `entry_date`
- `quantity`

差異：

- 快取隔離：和 `/analyze` 分開存在 `stock_analysis_cache.analysis_type`。
- GraphState 多了 entry/position 欄位。
- response 會多出 `position_analysis`。
- `build_position_risk_language()` 將 `recommended_action`、trailing stop、exit reason 轉為研究/風險語言。
- shared context consumer 是 `position_analysis`。

持股診斷要回答的是：「這個既有部位目前風險如何？」不是「現在要不要建立新倉？」

---

## 6. Portfolio lifecycle 怎麼實作

入口：`backend/src/ai_stock_sentinel/portfolio/router.py`

### 6.1 核心資料模型

| Model | Table | 用途 |
| ----- | ----- | ---- |
| `UserPortfolio` | `user_portfolio` | 持股主表，包含 active/closed、成本、數量、出場與已實現損益 |
| `PositionEvent` | `position_event` | initial entry、add entry、partial/full exit、manual adjustment |
| `PositionLifecyclePlan` | `position_lifecycle_plan` | 進場 thesis、setup、預期持有期、防守規則、加碼條件 |
| `TradeReview` | `trade_review` | 單筆已結案交易復盤 |
| `PositionLifecycleReview` | `position_lifecycle_review` | 整個 `position_group_id` 生命週期復盤 |

`position_group_id` 是 lifecycle 的主軸。同一筆交易生命週期中，初始進場、加碼、部分出場與結案都要能串回同一 group。

### 6.2 需求與實作對照

| 需求 | Endpoint | 實作重點 |
| ---- | -------- | -------- |
| 新增持股並記錄進場脈絡 | `POST /portfolio` | 建 `UserPortfolio`、寫 `initial_entry` event、可建立 lifecycle plan |
| 修改持股 | `PUT /portfolio/{portfolio_id}` | 更新主表欄位 |
| 加碼 | `POST /portfolio/{portfolio_id}/add-entry` | 更新加權平均成本、寫 `add_entry` event、記錄 plan adherence |
| 出場/部分出場 | `POST /portfolio/{portfolio_id}/close` | 計算手續費/稅/realized PnL；全出場關閉原 row，部分出場建立 closed row |
| 已結案列表 | `GET /portfolio/closed` | 查 inactive 且有 exit date 的 row |
| 事件時間線 | `GET /portfolio/groups/{position_group_id}/events` | 依 event date 排序 |
| 補填 lifecycle plan | `PUT /portfolio/{portfolio_id}/lifecycle-plan/backfill` | 只允許補填或更新 user_backfilled plan，不覆寫原始 event-time plan |
| 單筆交易復盤 | `POST /portfolio/{portfolio_id}/review` | 先補 market data，再產生 deterministic review payload |
| group lifecycle review | `POST /portfolio/groups/{position_group_id}/lifecycle-review` | 以完整事件與 plan 建立 lifecycle review |
| 風險摘要 | `GET /portfolio/risk-summary` | 整合 active rows、lifecycle plan、latest final raw data |

---

## 7. Daily Radar 怎麼實作

Daily Radar 是獨立產品表面，不是 `/analyze` 的批次版。

### 7.1 Internal run flow

入口：`daily_radar/router.py` 的 `POST /internal/daily-radar/run`

流程：

1. 選 universe：`select_daily_radar_universe()`。
   - same-day institutional leaders
   - recent accumulation/concentration leaders
   - local `StockRawData` 可支撐的日頻 technical trigger tracks
2. 讀 selected symbols 的 shared background context cache。
3. 對 selected symbols 補齊缺少的 OHLCV：`ensure_daily_radar_raw_rows()`。
4. 建立 market context：`YFinanceMarketIndexContextProvider`。
5. 呼叫 `run_daily_radar()`。
6. 寫入 `daily_radar_runs` 與 `daily_radar_candidates`。

### 7.2 Scoring flow

核心：`daily_radar/service.py`

```text
load records
  -> Stage 1 prefilter
  -> Stage 2 scoring
  -> attach background contexts
  -> cooldown
  -> explanations
  -> sort by observation_score
  -> replace candidates
```

| 模組 | 責任 |
| ---- | ---- |
| `prefilter.py` | hard gate 與初篩 |
| `scoring.py` | bucket score、risk labels、score breakdown |
| `market_context.py` | 大盤狀態 |
| `relative_strength.py` | 相對大盤強弱 |
| `cooldown.py` | 重複出現與冷卻狀態 |
| `explanations.py` | 候選解釋文字 |
| `repository.py` | run/candidate/shared context DB 存取 |

Daily Radar 不用 LLM 選股，不用 LLM 排名。LLM 未來若加入，只能潤飾文字，不能覆寫 bucket 或 score。

### 7.3 Forward validation 與 monthly rule review

| 需求 | Endpoint | 實作 |
| ---- | -------- | ---- |
| 驗證成熟候選 | `POST /internal/daily-radar/forward-validation/run` | 讀 production DB 中的 candidates 與 raw price series，寫 `daily_radar_forward_validation_results` |
| 月度 rule review | `POST /internal/daily-radar/rule-review/monthly` | 讀 validation results，產生 JSON + Markdown report |

對應 workflow：

- `.github/workflows/daily-radar.yml`
- `.github/workflows/daily-radar-chip-context.yml`
- `.github/workflows/daily-radar-rule-review.yml`

---

## 8. Shared Background Context 怎麼實作

主要 model：`SharedBackgroundContext`

主要 reader：`shared_context.py`

背景 context 類型目前包含：

- `weekly_major_holders`
- `lending`
- `full_margin`

寫入方式：

- `POST /internal/daily-radar/chip-context/update`
- daily lending/full margin 由 FinMind 背景 provider 更新
- weekly major holders 由 TDCC provider 更新
- row 以 `symbol + context_type + replay_key` upsert

讀取方式：

```python
read_shared_context_for_symbol(
    db,
    symbol=symbol,
    consumer="analyze|position_analysis|portfolio_diagnosis|lifecycle_review",
    reference_date=...,
    point_in_time=True|False,
)
```

重要邊界：

- `data_quality.blocking` 永遠是 `False`。
- stale/missing 只回 caveat，不阻斷主流程。
- lifecycle review 要用 point-in-time，不能用未來 context 改寫過去交易。
- read path 會尊重 `applicable_consumers`。

---

## 9. Cache 與資料持久化

| 層級 | Table | 用途 |
| ---- | ----- | ---- |
| L1 analysis cache | `stock_analysis_cache` | 保存 `/analyze`、`/analyze/position` full result；用 `analysis_type` 隔離 |
| L2 raw data cache | `stock_raw_data` | 保存 technical、institutional、fundamental raw payload |
| Daily log | `daily_analysis_log` | 每日分析紀錄與 strategy trace |
| Daily Radar | `daily_radar_runs`, `daily_radar_candidates` | 每日雷達 run 與候選 |
| Forward validation | `daily_radar_forward_validation_results` | 成熟候選驗證結果 |
| Portfolio lifecycle | `user_portfolio`, `position_event`, `position_lifecycle_plan`, `trade_review`, `position_lifecycle_review` | 持股與復盤 |

`STRATEGY_VERSION` 會讓舊 `stock_analysis_cache` 失效。改 rule-based strategy / scoring / classification 時，要評估是否需要 bump。

---

## 10. Authentication 與 internal API

使用者 API：

- Google OAuth：`auth/router.py`
- JWT dependency：`auth/dependencies.py`
- portfolio 與 analyze endpoint 會依賴 `get_current_user`

Internal API：

- Daily Radar run、chip context update、forward validation、rule review 都需要 `DAILY_RADAR_INTERNAL_TOKEN`。
- 舊的 `/internal/fetch-raw-data` 使用 internal API key dependency。

---

## 11. 測試怎麼找

常用測試：

```bash
cd backend
uv run pytest -q
```

按需求找測試：

| 需求 | 測試 |
| ---- | ---- |
| `/analyze` contract | `tests/test_api.py`, `tests/test_daily_radar_api_contract.py` |
| LangGraph | `tests/test_graph_builder.py`, `tests/test_graph_nodes.py`, `tests/test_nodes.py` |
| 技術指標 | `tests/test_context_generator.py`, `tests/test_daily_radar_scoring.py`, `tests/test_daily_radar_relative_strength.py` |
| 資料來源 | `tests/test_yfinance_client.py`, `tests/test_finmind_client.py`, `tests/test_institutional_flow.py`, `tests/test_fundamental_tools.py` |
| Portfolio | `tests/test_portfolio_router.py`, `tests/test_portfolio_history.py`, `tests/test_portfolio_fees.py` |
| Lifecycle / review | `tests/test_position_lifecycle_analysis.py`, `tests/test_trade_review.py` |
| Daily Radar | `tests/test_daily_radar_service.py`, `tests/test_daily_radar_api.py`, `tests/test_daily_radar_repository.py` |
| Shared context | `tests/test_daily_radar_background_context.py` |
| Release gate | `tests/test_investment_discipline_release_gate.py`, `tests/test_compatibility_deprecation_audit.py` |

---

## 12. 新需求應該改哪裡

| 新需求 | 先看 | 常改檔案 | 必同步文件 |
| ------ | ---- | -------- | ---------- |
| 新增 `/analyze` response 欄位 | `analysis/router.py`, `analysis/application/response_builder.py`, `graph/nodes.py` | `AnalyzeResponse`, `_build_response`, tests | `backend-api-technical-spec.md` |
| 改技術指標 | `analysis/metrics.py`, `graph/nodes.py` | metrics、signal summary、tests | architecture spec、backend API spec |
| 改持股診斷語意 | `analysis/position_scorer.py`, `analysis/router.py` | risk language、position response、tests | position diagnosis spec |
| 新增 portfolio lifecycle 欄位 | `portfolio/router.py`, `db/models.py` | model、migration、serializer、tests | position diagnosis spec、backend API spec |
| 改 Daily Radar ranking | `daily_radar/scoring.py` | scoring、prefilter、fixtures/tests | daily-stock-radar spec、roadmap/release decision |
| 新增背景 context | `daily_radar/background_context.py`, `shared_context.py` | provider、repository、labels、tests | daily-stock-radar spec、architecture spec |
| 改 monthly rule review | `daily_radar/rule_governance.py` | report builder、workflow、tests | daily-stock-radar spec、playbook |
| 改 DB schema | `db/models.py`, `alembic/versions/` | migration、tests、serializers | backend API spec、architecture spec |

---

## 13. 最小閱讀路線

如果只有 30 分鐘：

1. `docs/specs/ai-stock-sentinel-architecture-spec.md` 的「目前實作快照」
2. 本文件第 4 節 `/analyze`
3. 本文件第 7 節 Daily Radar
4. 本文件第 8 節 Shared Background Context
5. `backend/src/ai_stock_sentinel/analysis/router.py` 的 `/analyze` 與 `/analyze/position`

如果要能改功能：

1. 讀 `graph/builder.py` 與 `graph/nodes.py`
2. 讀 `daily_radar/service.py` 與 `daily_radar/scoring.py`
3. 讀 `portfolio/router.py`
4. 讀 `db/models.py`
5. 跑對應測試，確認自己理解的流程是目前實作而不是舊文件記憶
