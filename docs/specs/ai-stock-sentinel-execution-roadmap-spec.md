# AI Stock Sentinel 執行路線圖需求規格

> 類型：合併需求規格（Spec）
> 建立日期：2026-05-25
> 狀態：Active
> 目的：整併原 P0 / P1 / P2 / P3 階段需求文件，保留可執行需求、驗收條件與已決策事項，降低 specs 目錄文件數量。
> 整併來源：原 `p0-prerequisite-spec.md`、`p0-backtest-new-position-spec.md`、`p0-confidence-calibration-spec.md`、`p1-analyze-strategy-card-spec.md`、`p1-backtest-result-persistence-spec.md`、`p1-intraday-vs-close-split-spec.md`、`p2-llm-output-eval-spec.md`、`p2-strategy-versioning-spec.md`、`p3-portfolio-analyze-integration-spec.md`。

---

## 1. 文件定位

本文件只管理「分階段落地需求」。長期架構與跨模組系統設計仍以以下文件為準：

| 文件                                           | 職責                                           |
| ---------------------------------------------- | ---------------------------------------------- |
| `ai-stock-sentinel-architecture-spec.md`       | 核心分析架構、四維資料流、技術/籌碼/消息面規則 |
| `backend-api-technical-spec.md`                | 後端 API contract                              |
| `ai-stock-sentinel-position-diagnosis-spec.md` | 持股診斷視角                                   |
| `ai-stock-sentinel-automation-review-spec.md`  | 自動復盤、資料循環與每日紀錄                   |

規則：

- 已確認的系統事實放在長期架構文件。
- 階段需求、驗收條件、暫緩或否決決策放在本文件。
- 執行 SOP 放在 `docs/development-execution-playbook.md`，本文件只保留需求來源與驗收標準。

---

## 2. Roadmap 摘要

| 階段 | 主題                                       | 狀態     | 目的                                      |
| ---- | ------------------------------------------ | -------- | ----------------------------------------- |
| P0   | 回測地基、新倉回測、信心分數校準           | Active   | 讓新倉策略與信心分數可被歷史資料驗證      |
| P1   | 策略卡體驗、盤中 guardrail、回測結果持久化 | Active   | 提升策略可讀性、安全性與回測結果查詢能力  |
| P2   | LLM 輸出評測、策略版本化                   | Active   | 控制 LLM 敘事品質，讓策略版本可追蹤與比較 |
| P3   | 新倉/持股體驗整合                          | Rejected | 2026-03-19 已否決，不實作                 |
| DR1  | 官方籌碼背景資料                           | Approved | 解除 FinMind 融資融券與借券逐檔 request  |
| DR2  | 台股 OHLCV 本地歸檔                        | Approved | 解除 yfinance 日線行情依賴與重複抓取      |
| DR3  | 基本面官方快取與版本庫                     | Approved | 解除分析流程 FinMind 基本面正常路徑依賴   |

---

## 3. P0：回測與信心校準

### 3.1 背景

新倉策略已可輸出 `strategy_type`、`conviction_level`、`action_plan` 與 `signal_confidence`，但若沒有回測與版本標記，無法回答高信心是否真的對應高勝率，也無法比較不同策略版本。

### 3.2 目標

| #     | 目標                                                                        |
| ----- | --------------------------------------------------------------------------- |
| P0-G1 | 建立新倉策略回測標準指標：勝率、報酬、持有週期                              |
| P0-G2 | `backtest_win_rate.py` 支援 `--mode new-position`，且 `position` 模式不回歸 |
| P0-G3 | 策略輸出與回測記錄帶有 `strategy_version`                                   |
| P0-G4 | 建立 `signal_confidence` 分桶與新倉勝率校準流程                             |
| P0-G5 | 資料源成功/失敗可用結構化 log 追蹤                                          |

### 3.3 基礎建設需求

| 編號  | 需求                                                                                                  |
| ----- | ----------------------------------------------------------------------------------------------------- |
| P0-F1 | `config.py` 定義 `STRATEGY_VERSION = "1.0.0"`                                                         |
| P0-F2 | `DailyAnalysisLog` 與 `StockAnalysisCache` 具 nullable `strategy_version VARCHAR(20)` 欄位；正式校準樣本另寫入 append-only `analysis_calibration_samples` |
| P0-F3 | 每次分析寫入 DB 時同步寫入目前 `STRATEGY_VERSION`；舊資料不回填                                       |
| P0-F4 | Alembic migration 支援 upgrade / downgrade                                                            |
| P0-F5 | `institutional_flow/router.py`、`yfinance_client.py`、`rss_news_client.py` 成功/失敗路徑輸出 JSON log |
| P0-F6 | `provider_success` log 包含 `event`、`provider`、`symbol`、`is_fallback`                              |
| P0-F7 | `provider_failure` log 包含 `event`、`provider`、`symbol`、`error_code`                               |

### 3.4 新倉回測定義

| 指標     | 定義                                                                                         |
| -------- | -------------------------------------------------------------------------------------------- |
| 樣本來源 | append-only `analysis_calibration_samples`，僅收錄 `/analyze`、`strategy_type IN ('short_term', 'mid_term')` 且 `analysis_is_final = TRUE` |
| 排除樣本 | `strategy_type = 'defensive_wait'`                                                           |
| 持有週期 | 5 / 10 / 20 交易日                                                                           |
| 勝率     | 訊號日起第 N 個交易日收盤相對訊號日收盤漲幅 > +3%                                            |
| 敗率     | 漲幅 < -3%                                                                                   |
| 平手     | 漲幅介於 -3% ~ +3%（含端點），單獨列出，不計入勝敗                                           |
| 報酬     | `(pN - p0) / p0 * 100`                                                                       |
| 樣本限制 | 預設 `analysis_is_final = TRUE`；可加 `--require-final-raw-data`                             |

### 3.5 新倉回測輸出需求

| 編號  | 需求                                                                                          |
| ----- | --------------------------------------------------------------------------------------------- |
| P0-B1 | `--mode` 接受 `position`（預設）與 `new-position`                                             |
| P0-B2 | `--mode position` 行為與既有輸出相容                                                          |
| P0-B3 | 新倉模式支援 `--hold-days`，預設 5，可輸出 5 / 10 / 20 多週期矩陣                             |
| P0-B4 | 依 `strategy_type` 輸出 n、勝率、平局率、敗率、平均報酬                                       |
| P0-B5 | 依 `conviction_level` 輸出 n、勝率、平均報酬                                                  |
| P0-B6 | 依 `evidence_scores.total` 分箱 `<2`、`2-3`、`>=4` 輸出 n 與勝率                              |
| P0-B7 | 輸出 `signal_confidence` vs 5 日漲幅、`evidence_scores.total` vs 5 日漲幅的 Pearson r / p / n |
| P0-B8 | 有效樣本 < 5 時跳過 Pearson 並提示；分箱樣本 < 5 時標注樣本不足                               |

### 3.6 信心分數校準需求

| 編號  | 需求                                                                                                                           |
| ----- | ------------------------------------------------------------------------------------------------------------------------------ |
| P0-C1 | 分析 confidence 分桶 `<60`、`60-70`、`70-80`、`80+` 的 5 / 10 日勝率與平均報酬                                                 |
| P0-C2 | 若分桶勝率不單調遞增，輸出異常分桶警告                                                                                         |
| P0-C3 | `scripts/analyze_confidence_breakdown.py` 依 `inst_flow`、`sentiment_label`、`technical_signal` 分組輸出 n、5 日勝率、平均報酬 |
| P0-C4 | 若需調權，月度 workflow 先產出 AES-256 加密的 GitHub Actions artifact，人工下載與審核後才可改 `ConfidenceScoringConfig`；production report 不寫入 public issue 或 main branch |
| P0-C5 | 調權只改提案列出的常數，並同步 bump `STRATEGY_VERSION` minor version                                                           |
| P0-C6 | 調權後重跑完整回測；高分桶勝率需 >= baseline 同分桶勝率，否則文件說明原因                                                      |
| P0-C7 | `docs/development-execution-playbook.md` 保留信心分數調權流程 SOP                                                              |

### 3.7 診斷矩陣

| 現象                                                 | 判斷                                 |
| ---------------------------------------------------- | ------------------------------------ |
| 高分桶（80+）勝率低於低分桶（<60）                   | 需要調權；高分條件可能沒有新倉預測力 |
| 各分桶勝率差距 < 5%                                  | 需要重新評估 confidence 區分力       |
| `distribution` 勝率高於 `institutional_accumulation` | 籌碼方向性設計可能有問題             |
| Pearson r < 0.1 且 p > 0.1                           | confidence 與漲幅幾乎無關            |
| 趨勢正確但差距僅 10-20%                              | 可接受，訊號本身有噪音               |
| 樣本不足                                             | 暫不調權，等待更多樣本               |

### 3.8 P0 驗收條件

| #      | 驗收條件                                                                   |
| ------ | -------------------------------------------------------------------------- |
| P0-AC1 | `/analyze` 新記錄的 `strategy_version` 正確寫入                            |
| P0-AC2 | migration 可 downgrade / upgrade                                           |
| P0-AC3 | `python scripts/backtest_win_rate.py --mode new-position --days 30` 可執行 |
| P0-AC4 | 無 `--mode` 的既有 position 回測輸出不回歸                                 |
| P0-AC5 | log 可 grep 到 `provider_success` / `provider_failure` JSON 行             |
| P0-AC6 | 新倉回測輸出 strategy / conviction / evidence 三組分箱與 Pearson 分析      |
| P0-AC7 | 信心分桶報告已產出；若調權，提案、版本、post-calibration 回測皆存在        |

### 3.9 每日驗證與月度雙軌治理

- `.github/workflows/daily-radar.yml` 在 OHLCV／market context 後呼叫 Daily Radar due validation；`.github/workflows/analysis-forward-validation.yml` 每日呼叫一般分析 due validation。任一軌失敗會讓對應 workflow 失敗，但不回滾已發布的 Daily Radar 或已完成的一般分析。
- 一般分析第一版只將 `.TW` / `.TWO` final `/analyze` 寫入 `analysis_calibration_samples`，並以 `analysis_forward_validation_results` 保存去識別化 replay input、5 / 10 / 20 交易日 outcome 與 skip reason；其他市場不進 TW / TAIEX cohort，也不得保存 user id、使用者筆記、新聞全文或 LLM 長文。
- `.github/workflows/monthly-analysis-calibration.yml` 每月 6 日產出單一 AES-256 加密 Actions artifact，內含 Daily Radar 與一般分析 JSON、Markdown Actions 及 SHA-256 manifest。
- 月報使用最近六個 20 日窗口已完整評估月份；前五個為 training，最新一個為 holdout，並以 `run_date` / `record_date` 作固定 seed block bootstrap。成熟度與 validated coverage 分開呈現。
- `min_sample_count` 逐窗口計算 distinct signal / candidate；training 固定至少 20 個日期 blocks、holdout 至少 5 個日期 blocks，避免同日股票或同一訊號的三個窗口被誤當獨立樣本。
- Replay coverage 必須整體與每個入選月份均達 90%，否則 candidate eligibility 固定為 `replay_coverage_below_threshold`；一般分析分母只涵蓋 optimizer scope 的 `short_term` / `mid_term`，刻意排除的策略不算 replay 缺漏。
- 舊資料沒有正式 replay input 時標記 `replay_input_incomplete`，不得根據輸出猜回輸入。
- Final `/analyze` cache 保存去識別化精簡 replay payload，讓 capture 暫時失敗時可由後續 final cache hit 冪等補寫；無 payload 的舊 cache 維持跳過。
- Daily Radar 第一版只測試 ranking component weights 與 secondary threshold；一般分析第一版只測試正向 sentiment / institutional / technical / resonance points。風險扣分、負向 evidence、rule scores、prefilter 與 strategy generator 規則全部鎖定。
- 每個 candidate config 只改一個參數、一個 step；報表只輸出 `auto_change_eligible`，不直接修改 production、建立 PR、merge 或 deploy。
- 加密 artifact retention 為 30 天；每月需下載保存，權重審查可在累積六個成熟月份後進行。

### 3.10 校準可擴充性

狀態：**Completed**。P0-CAL-S1 與 P0-CAL-S2 已提前完成；未改變 scoring、window maturity、API response schema、validation version 或自動調權安全邊界。

| 順序 | 編號 | 狀態 | 落地範圍 |
| ---- | ---- | ---- | -------- |
| 1 | P0-CAL-S1 | Completed | Daily Radar 與一般分析月報先以 SQL monthly aggregation 計算 watermark 並選出 cohort，再以明確月份 predicate 只載入最近六個成熟月份的 optimizer replay / validation detail；Daily Radar requested-month diagnostics 使用獨立單月 bounded query |
| 2 | P0-CAL-S2 | Completed | `ai_stock_sentinel.calibration.forward_validation` 統一 due-window policy、forward outcome evaluation、price-series normalization 與 benchmark completeness policy；`calibration.repository` 統一共用 price source，兩軌各自提供 feature adapter |

#### P0-CAL-S1 驗收條件

- 同一份 frozen production-like fixture 的 cohort months、watermark、coverage、candidate eligibility 與目前輸出完全一致。
- SQL trace 可證明 detailed replay query 具有所選 cohort 的日期上下界，不再從 2000 年起載入所有 sample / validation rows。
- 空月份、少於六個成熟月份、skipped retry、同日 rerun 與 `replay_input_incomplete` 行為不變。
- 提供 query-count 與 wall-clock benchmark；新路徑不得比 baseline 增加 query 次數，且大量歷史資料 fixture 的載入 rows 必須受六個 cohort months 限制。
- 不需要 schema migration；rollback 只需還原 query path。

#### P0-CAL-S2 驗收條件

- `analysis.calibration` 與 general calibration router 不再 import `daily_radar.forward_validation` 或 `daily_radar.auth`。
- Shared core 不得 import Daily Radar scoring、rule registry、candidate ORM 或一般分析 confidence scorer；feature-specific snapshot 與報表維度由 adapter 注入。
- Daily Radar 與一般分析在相同 fixture 上的 target date、forward return、benchmark excess、MFE / MAE、hit、skip reason 與 due-window 結果保持 byte-for-byte 等價。
- 既有四個 internal calibration endpoints、request / response schema、validation version、workflow 與 artifact 格式不變。
- Provider failure 仍讓 workflow 失敗；既有 `validated` window 保持 terminal，retryable `skipped` window 仍會於後續 due run 重試。
- 不需要資料搬移；rollback 可逐 track 將 adapter 指回既有 evaluator。

驗證已加入 19 個歷史月份的 production-like fixture：每軌月報固定三個 SELECT（requested month detail、monthly watermark aggregation、selected cohort detail），optimizer detail 維持六個月份；同一價格 fixture 亦驗證兩軌 target date、forward / benchmark excess、MFE / MAE、hit 與 skip behavior 等價。若未來 benchmark 顯示 replay CPU 而非資料載入成為主要耗時，再另開 replay cache / vectorization 計劃。

---

## 4. P1：產品體驗與回測持久化

### 4.1 策略卡升級

| 編號   | 需求                                                                     |
| ------ | ------------------------------------------------------------------------ |
| P1-UI1 | Analyze 策略卡採四段式：建議動作、主要理由、關鍵價位、失效條件           |
| P1-UI2 | `suggested_position_size` 有值時顯示於關鍵價位段，空值不渲染             |
| P1-UI3 | `upgrade_triggers` / `downgrade_triggers` 放在預設收合的「條件變化」區塊 |
| P1-UI4 | 兩組 triggers 皆空時不渲染區塊；只有一組有值時只顯示有值部分             |
| P1-UI5 | 關鍵價位使用帶底色或邊框的卡片，與推論性列表視覺區隔                     |
| P1-UI6 | `action_plan = null` 時策略卡不渲染；缺值優雅降級                        |
| P1-UI7 | 盤中免責聲明位於策略卡底部，不遮蔽主要資訊                               |

### 4.2 盤中 vs 收盤策略分流

| 編號  | 需求                                                                                           |
| ----- | ---------------------------------------------------------------------------------------------- |
| P1-I1 | `is_final=False` 時 `conviction_level` 最高為 `medium`                                         |
| P1-I2 | `is_final=False` 且原始計算為 `high` 時降為 `medium`，`low` 保持 `low`                         |
| P1-I3 | `is_final=False` 時 `suggested_position_size` 固定為「盤中觀察，建議等待收盤確認後再評估部位」 |
| P1-I4 | `is_final=False` 不輸出「全倉」或「積極建倉」等積極文字                                        |
| P1-I5 | `is_final=True` 的部位規模邏輯不受影響                                                         |
| P1-I6 | 前端 `is_final=false` 時策略卡標題區顯示「盤中版」amber pill，並與 conviction badge 並列       |

`suggested_position_size` 收盤版規則：

| 情況              | 輸出                |
| ----------------- | ------------------- |
| defensive_wait    | 建議暫不建立新倉    |
| low conviction    | 小試水溫（5% 以下） |
| medium conviction | 輕倉試探（10-15%）  |
| high conviction   | 標準部位（20-30%）  |

### 4.3 回測結果持久化

| 編號   | 需求                                                                |
| ------ | ------------------------------------------------------------------- |
| P1-BT1 | 新增 `BacktestRun` 與 `BacktestResult` ORM 模型及 Alembic migration |
| P1-BT2 | 每次 `backtest_win_rate.py` 執行後寫入一筆 `backtest_run`           |
| P1-BT3 | 每個樣本結果寫入一筆 `backtest_result`                              |
| P1-BT4 | `--output-json` 移除，或保留但 deprecated 且不寫檔                  |
| P1-BT5 | CLI 摘要輸出保留；DB 寫入失敗時輸出明確錯誤                         |
| P1-BT6 | `strategy_version` 從 `config.STRATEGY_VERSION` 寫入 `backtest_run` |
| P1-BT7 | 移除 `backend/backtest-results/` 與 `.gitignore` 相關規則           |

`backtest_run` 必要欄位：`run_date`、`mode`、`hold_days`、`days_lookback`、`strategy_version`、`total_samples`、`win_count`、`loss_count`、`draw_count`、`skip_count`、`win_rate`、`created_at`。

`backtest_result` 必要欄位：`run_id`、`symbol`、`signal_date`、`p0_price`、`pN_price`、`pct_change`、`outcome`、`skip_reason`、`signal_confidence`、`conviction_level`、`strategy_type`、`action_tag`、`log_id`。

### 4.4 P1 驗收條件

| #      | 驗收條件                                                                  |
| ------ | ------------------------------------------------------------------------- |
| P1-AC1 | 策略卡四段式結構可目視確認，缺值不報錯                                    |
| P1-AC2 | `suggested_position_size` 與 triggers 顯示/收合符合規則                   |
| P1-AC3 | 盤中 `conviction_level` 與 `suggested_position_size` guardrail 有單元測試 |
| P1-AC4 | 前端 `is_final=false` 顯示「盤中版」，`is_final=true` 不顯示              |
| P1-AC5 | `alembic upgrade head` 後 backtest tables 存在，downgrade 後消失          |
| P1-AC6 | 回測執行後 `backtest_run` / `backtest_result` 筆數與 console 摘要一致     |

---

## 5. P2：LLM 評測與策略版本化

### 5.1 LLM 輸出評測

| 編號  | 需求                                                                                                       |
| ----- | ---------------------------------------------------------------------------------------------------------- |
| P2-E1 | `backend/tests/fixtures/llm_eval_cases.json` 至少含 5 類案例：正常、維度越界、結論衝突、過度武斷、造假數字 |
| P2-E2 | `scripts/eval_llm_output.py` 支援 `--cases`，預設讀 fixture                                                |
| P2-E3 | 支援 `--dry-run`，不呼叫 LLM，改用 `mock_llm_output` 跑 checks                                             |
| P2-E4 | 每個 check 結果為 `pass`、`warn`、`fail`                                                                   |
| P2-E5 | 支援 `--output-json` 輸出完整報告                                                                          |
| P2-E6 | `fail_count > 0` 時 exit code 1                                                                            |
| P2-E7 | `langchain_analyzer.py` 計算 `_SYSTEM_PROMPT` MD5 hash，存為 `PROMPT_HASH`                                 |
| P2-E8 | 評測報告記錄 `prompt_hash`                                                                                 |

評測規則：

| Check                       | 規則                                                                    |
| --------------------------- | ----------------------------------------------------------------------- |
| `json_valid`                | 可 `json.loads()`，且含 `final_verdict`、`tech_insight`、`inst_insight` |
| `no_cross_dimension`        | `tech_insight` 不提法人買賣超；`inst_insight` 不提 RSI / 均線           |
| `verdict_conviction_align`  | final verdict 語氣與 conviction 大致一致                                |
| `no_overconfident_language` | 不含「必然」「確定」「100%」「一定會」等字串；含則 warn                 |
| `no_fabricated_source`      | 不引用輸入未出現的研究機構或分析師來源；含則 fail                       |

### 5.2 策略版本化

採用 `MAJOR.MINOR.PATCH`：

| 版次  | 觸發條件                                                                                                      |
| ----- | ------------------------------------------------------------------------------------------------------------- |
| PATCH | docstring、log 格式、非邏輯性重構                                                                             |
| MINOR | 修改 `confidence_scorer.py` 常數、`generate_action_plan()` 文字模板、`_determine_conviction_level()` 降級閾值 |
| MAJOR | 修改 `generate_strategy()` 核心 evidence scoring、策略分類規則、`confidence_scorer.py` 計算架構               |

不需 bump：LLM prompt 修改（由 `prompt_hash` 追蹤）、前端 UI、非策略邏輯 DB schema。

| 編號  | 需求                                                                                           |
| ----- | ---------------------------------------------------------------------------------------------- |
| P2-V1 | `AnalyzeResponse` 新增 nullable `strategy_version`                                             |
| P2-V2 | `/analyze` 回傳 `config.STRATEGY_VERSION`；快取命中時讀快取記錄版本                            |
| P2-V3 | 快取 `strategy_version != STRATEGY_VERSION` 或 `NULL` 時視為版本失效，觸發重分析但不刪除舊記錄 |
| P2-V4 | 版本失效輸出 info log，包含 `symbol`、`cache_version`、`current_version`                       |
| P2-V5 | `backtest_win_rate.py` 支援 `--strategy-version`，可接受逗號分隔多版本                         |
| P2-V6 | `--strategy-version NULL` 只回測舊記錄；不指定則不過濾版本                                     |
| P2-V7 | `docs/development-execution-playbook.md` 保留策略版本遞增 SOP                                  |

### 5.3 P2 驗收條件

| #      | 驗收條件                                                           |
| ------ | ------------------------------------------------------------------ |
| P2-AC1 | `python scripts/eval_llm_output.py --dry-run` 可執行並印出案例統計 |
| P2-AC2 | 手動注入跨維度輸出時 check 會 fail                                 |
| P2-AC3 | `PROMPT_HASH` 可 import，評測報告含 `prompt_hash`                  |
| P2-AC4 | `/analyze` response 含 `strategy_version`                          |
| P2-AC5 | 修改 `STRATEGY_VERSION` 後舊快取失效，還原後快取恢復正常命中       |
| P2-AC6 | `--strategy-version 1.0.0` 只回測對應版本記錄                      |

---

## 6. P3：新倉/持股體驗整合（已否決）

### 6.1 決策

2026-03-19 否決，不實作。

否決理由：原假設「從 Portfolio 無法跳到 Analyze」不是實際痛點。使用者的持股必定是從 Analyze 頁手動加入，代表已看過新倉分析；因此新增 Portfolio → Analyze 導航與 Analyze 已持有橫幅不符合當前產品優先級。

### 6.2 被否決範圍

- Portfolio 持股卡新增「查看新倉分析」按鈕。
- `/portfolio → /analyze?symbol=XXXX` 自動查詢流程。
- Analyze 頁「已持有」提示橫幅。
- 已持有橫幅中的成本價、現價、損益與「查看持股診斷」連結。

若未來重新評估，需另開新 spec，重新驗證使用者路徑是否真實存在。

---

## 7. 資料來源韌性三階段（2026-08-07 核准）

實作狀態（2026-08-07）：DR1、DR2、DR3 程式、additive migrations、internal endpoints、GitHub Actions、環境開關與自動測試已落地；所有 provider mode 仍以既有來源為部署預設。Production 暖機、shadow comparison 與模式切換依 7.5 執行，尚未因程式合併而自動啟用。

### 7.1 共同決策

- 正式 runtime 不使用 MCP 作為市場資料 provider；MCP 僅能作探索或人工查詢。正式資料流採官方公開 API、正規化、本地資料庫及明確 fallback。
- 三階段必須可以獨立 migration、部署、切換與回滾；新增資料表保留，回滾只切 provider mode，不做破壞性資料刪除。
- 不改既有 public response contract，不把新增背景或基本面資料加入 Daily Radar ranking、confidence score、action、verdict 或 classification。
- 支援範圍先限定四位數 `.TW` / `.TWO` 普通股；ETF、權證、海外市場保留既有 provider fallback。
- 每個官方來源都要保存來源、資料日期、抓取時間、資料品質與 missing reason；合法的零值或 no-data 不可誤判成 provider failure。

### 7.2 DR1：官方籌碼背景資料

來源與行為：

- 上市融資融券使用 TWSE `MI_MARGN`，上櫃使用 TPEX 融資融券餘額；借券使用 TWSE `t13sa710`，`weekly_major_holders` 繼續使用 TDCC。
- 融資歷史查詢依市場使用不同日期格式：TWSE 為 `YYYYMMDD`，TPEX 為 `YYYY/MM/DD`；lookback 只計入有市場資料且不重複的 payload date，避免同一交易日被重複當成多日變化。
- 以市場級資料集取代 `selected symbols × context types` 的 FinMind 逐檔 request；full margin 最多回看 10 個交易日／37 個 calendar days，lending 依官方限制分成 7 日區間。
- 保存既有 `shared_background_contexts` payload keys、consumer contract、單位及 replay key 語意，不新增 schema migration。
- `DAILY_RADAR_BACKGROUND_PROVIDER_MODE` 支援 `finmind_only`、`official_first`、`official_only`。初次部署與緊急回滾使用 `finmind_only`；完成比對後切 `official_first`。
- `official_first` 只在 request、parser 或整個 market-date 失敗時 fallback；個別股票沒有合法資料時寫入 missing payload，不逐檔呼叫 FinMind。

驗收：支援市場在正常刷新時 FinMind request 為 0，整體官方 request 數不隨 selected universe 250 檔線性成長；既有 background context API 與 Daily Radar scoring 測試不變。

### 7.3 DR2：台股 OHLCV 本地歸檔

新增 `taiwan_daily_bars`：

- 唯一鍵為 `(symbol, trade_date, dataset, adjustment_mode)`；保存 market、name、OHLC、volume、amount、source provider/dataset、fetched_at、`adjustment_mode=unadjusted` 與 `is_final`。
- 上市資料使用 TWSE `MI_INDEX` 全市場日行情；上櫃資料使用 TPEX 上櫃股票每日收盤行情。
- `POST /internal/daily-radar/refresh-market-bars` 在台灣時間 18:30 預抓，既有 22:30 `refresh-ohlcv` 在當日 market bars 不完整時自我修復。
- 官方 market-bar transport、HTTP 408/425/429/5xx 與 JSON decode 暫時性錯誤最多嘗試三次並採 exponential backoff；TPEX 同一 refresh 內序列化 request，避免 180-day backfill 以四個 workers 同時壓迫單一官方端點。永久 4xx、schema/date mismatch 與非 no-data response 仍 fail closed。
- `DAILY_RADAR_TW_OHLCV_PROVIDER_MODE` 支援 `yfinance_only`、`official_first`、`official_only`；正式切換前以 180 calendar days 手動 backfill 暖機。
- Phase 1 AVWAP 與基本面季末價格優先讀本地 120 calendar days，至少需要 60 根 final trading bars 且最新日期等於 `run_date`；不足或不支援股票才走既有 provider。Daily Radar technical indicators 必須維持 adjusted price 語意；目前 `taiwan_daily_bars` 僅保存 `unadjusted` 官方原始行情，因此 technical path 不得用它取代 yfinance adjusted history，直到另有可驗證的 adjusted archive。
- `taiwan_daily_bars` 是全域市場行情，不可保存 user id、entry date、avg cost 或持股 anchor；不得重用 `phase1_avwap_snapshots`。

驗收：AVWAP 與基本面季末價格暖機後可由官方 archive 供應；Daily Radar technical path 明確固定 `auto_adjust=true` 且不讀 unadjusted archive；backfill 冪等、可續跑、跳過非交易日；migration 可 upgrade/downgrade；原始行情與既有 compatibility indicators / `technical_profile` contract 不回歸。

### 7.4 DR3：基本面官方快取與版本庫

資料來源與 provider：

- TWSE/TPEX 六種產業財務報表 OpenAPI 提供市場級當季累計 EPS；上市股利使用 TWSE `t187ap45_L`，上櫃已除權息現金股利使用 TPEX `bulletin/exDailyQ`。
- TPEX `mopsfin_t187ap39_O` 抽查存在明顯資料落後，不得作唯一上櫃股利來源；尚未除息或歷史不足時，`official_cache_first` 可對單一股票做一次 FinMind bootstrap 並持久化。
- 新增 `OfficialCachedFundamentalProvider`；`/analyze` 與 `/analyze/position` 正常路徑只讀 DB，不直接呼叫官方 API。
- `FUNDAMENTAL_PROVIDER_MODE` 支援 `finmind_only`、`official_cache_first`、`official_cache_only`。`official_cache_only` 資料不足時回傳 partial fundamental context 與 warning，不中止整體分析。

新增 `company_fundamental_periods`：保存 symbol/market、fiscal year/quarter、statement scope、industry schema、官方累計 EPS、FinMind 離散季度 EPS、report date、first/last observed、availability quality、來源、payload hash 與 raw payload；財報修訂追加版本，不覆蓋舊值。官方單季 EPS 依查詢當下選中的 point-in-time revisions 動態推導，避免前期重編回頭改寫既有後期 revision。

新增 `company_dividend_events`：保存股利年度與涵蓋期間、決議狀態、董事會／股東會／除權息日期、盈餘／法定盈餘公積／資本公積現金股利、合計現金股利、first/last observed、來源、payload hash 與 raw payload；重疊期間無法消歧時 fail closed。

計算規則：

- 官方累計 EPS 轉單季：Q1 等於 Q1 累計；Q2/Q3/Q4 分別扣除前一累計期間。前期缺漏時不猜值，該季及 TTM 標記 unavailable。
- FinMind `TaiwanStockFinancialStatements` bootstrap 的 `EPS` 是單季值，直接保存於 `quarter_eps`，不得先寫成累計值再相減。
- TTM 只使用最新四個連續離散季度，不得因最新季度缺值而退回更舊的四季窗口並偽裝成最新 TTM；目前 PE 僅在 TTM EPS > 0 時計算。Q1 單季 EPS 等同當年 Q1 累計，因此可作為官方 Q2 累計的前期基準；其他官方累計季度因缺少前期官方累計而無法推導時，若同期間已有 FinMind bootstrap 的直接單季 EPS，可使用該直接值並保留 FinMind provenance，沒有直接值時維持 unavailable。
- 歷史 PE 最多 24 季，季末價格優先讀 DR2 `taiwan_daily_bars`，資料不足才使用 yfinance fallback；至少四個有效樣本才產生估值帶。
- 年度股利優先使用完整年度事件，否則加總互不重疊季度／半年事件；FinMind bootstrap 必須解析 `year` 的民國年與季度範圍，無法確認期間時保持 unbounded 並 fail closed，不得把每筆配息偽裝成完整年度，也不得用股價乘殖利率反推現金股利。
- 官方與 FinMind 同時保存相同股利涵蓋期間時，先以官方事件消除跨來源重疊；同一優先來源仍有無法消歧的重疊時維持 fail closed。基本面與 AVWAP 的公開 provenance 必須反映實際使用的 official、bootstrap 或 fallback provider，不得只標示 routing wrapper。
- `first_observed_at` 是 point-in-time availability boundary；資料庫以 UTC 保存，但與 Daily Radar `run_date` 比較前必須換算成 `Asia/Taipei` 日期，避免台北隔日早晨取得的 revision 洩漏到前一交易日。FinMind 歷史 bootstrap 標記 `historical_unknown`，可支援目前估值帶，不可進入要求 point-in-time 正確性的歷史 replay/backtest。
- 保持現有 `ttm_eps`、`pe_current`、PE band/percentile、`annual_cash_dividend`、`dividend_yield`、`yield_signal`、source 與 warning public contract。

內部流程：

- `POST /internal/fundamentals/refresh` 每日 07:15 更新市場級財報與股利；dataset 可部分成功、冪等提交，最多四個並行 request、45 秒 timeout、兩次 retry。
- `POST /internal/fundamentals/backfill` 以 managed symbol universe、每批最多 10 檔及 server-owned `after_symbol` 游標執行；managed universe 必須合併 active holdings、watchlist、最新 prepared universe 與最近一次已完成 `refresh-ai-evidence` 日期的 final 支援台股 AI raw pool。第一頁以批次 archive queries 只保留至少一個 history lane 不完整的 symbol，並把完整 symbol snapshot、raw-pool 日期及下一個 cursor 持久化為 `fundamental_backfill_jobs`；所有後續頁必須以 `job_id` 鎖定同一 job、驗證 request cursor 與 job cursor 一致，只有整頁成功才能原子前移，不得重新查詢 live universe，completed job 不得重新執行。指定 raw-pool 日期也必須對應已完成的 `refresh-ai-evidence`；日期未完成、job 不存在、cursor 未帶 job、job 已完成或 cursor 不一致時 fail closed。已具備足夠 EPS 歷史或完整年度股利的 lane 不得重複呼叫 FinMind。每小時最多 120 次 FinMind request。GitHub workflow 必須在 partial failure 前先輸出 job/cursor；達到六批上限且仍有下一頁時必須以 `BACKFILL_NEXT_AFTER_SYMBOL`、`BACKFILL_JOB_ID` 與 step summary 回報 cursor/job、非零結束，下一次以 `backfill_after_symbol`、`backfill_job_id` 明確續跑。
- PostgreSQL revision 寫入使用 unique constraint 對應的 `ON CONFLICT DO UPDATE`，避免同一冷快取股票併發 bootstrap 時因先查後寫競態讓其中一個分析失敗。
- 市場級官方財報若回傳已知 schema、只有報表日期非空且公司／期間／財務欄位全空的官方占位列，視為尚未發布並回報 `datasets_skipped` / `skipped_datasets`，保留既有 cache；完全空 payload、缺少已知 identity fields、含非空業務欄位卻無法正規化，仍視為 dataset failure，不得靜默沿用舊 cache。TPEX 當日除權息事件可合法為空，維持 dataset-specific no-data 語意。
- 沿用 `DAILY_RADAR_INTERNAL_TOKEN` 的 fail-closed internal auth，不新增 provider key 或 secret。

驗收：12 種財報 schema alias、民國年與數值清理、官方累計轉單季、FinMind 單季 EPS 保真、TTM 連續性、財報修訂、季度／年度股利期間、point-in-time、官方快取零 FinMind happy path、一次性 fallback 持久化、併發 upsert、空資料集診斷、可續跑 workflow、`official_cache_only` graceful degradation、DR2/yfinance 價格切換、internal auth、partial failure、migration 與既有 API contract 都有自動測試。正式切換前仍須用代表性上市櫃及六種產業公司雙軌比對。

### 7.5 上線順序與安全邊界

1. DR1 先以 `finmind_only` 部署，官方 shadow refresh 通過後切 `official_first`。
2. DR2 建表並回補至少 180 calendar days，再切 `official_first`。
3. DR3 建表、暖機 managed universe、完成 EPS 雙軌比對，再切 `official_cache_first` 並觀察至少三個交易日。
4. Zeabur merge 會自動部署並執行 Alembic；每個 additive migration 合併前必須在 disposable PostgreSQL 執行 upgrade/head 驗證。若 migration 需要 destructive operation 或人工資料準備，合併前必須停止並另行確認。

---

## 8. 跨階段文件與 SOP

| 主題                | 文件                                               |
| ------------------- | -------------------------------------------------- |
| 信心分數調權流程    | `docs/development-execution-playbook.md`           |
| 策略版本遞增 SOP    | `docs/development-execution-playbook.md`           |
| 回測執行與結果解讀  | 本文件 P0 / P1 + `docs/research/backtest-results/` |
| LLM prompt 品質監控 | 本文件 P2 + `backend/eval-results/`                |

---

## 9. 文件維護規則

- 新增階段需求時，優先加入本文件，不再新增 `p0-*` / `p1-*` 形式的獨立 spec。
- 若某階段需求成為長期系統事實，移入對應長期 spec，並在本文件保留簡短決策紀錄。
- 被否決或暫緩需求不可刪除決策理由；應保留在「決策」段落，避免重複討論。
- 合併文件只保留可執行需求與驗收條件，過程討論與開放探索留在 `docs/plans/` 或 agent 對話中。
