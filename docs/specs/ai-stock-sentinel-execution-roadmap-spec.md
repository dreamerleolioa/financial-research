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
| P0-F2 | `DailyAnalysisLog` 與 `StockAnalysisCache` 具 nullable `strategy_version VARCHAR(20)` 欄位            |
| P0-F3 | 每次分析寫入 DB 時同步寫入目前 `STRATEGY_VERSION`；舊資料不回填                                       |
| P0-F4 | Alembic migration 支援 upgrade / downgrade                                                            |
| P0-F5 | `institutional_flow/router.py`、`yfinance_client.py`、`rss_news_client.py` 成功/失敗路徑輸出 JSON log |
| P0-F6 | `provider_success` log 包含 `event`、`provider`、`symbol`、`is_fallback`                              |
| P0-F7 | `provider_failure` log 包含 `event`、`provider`、`symbol`、`error_code`                               |

### 3.4 新倉回測定義

| 指標     | 定義                                                                                         |
| -------- | -------------------------------------------------------------------------------------------- |
| 樣本來源 | `DailyAnalysisLog.strategy_type IN ('short_term', 'mid_term')` 且 `analysis_is_final = TRUE` |
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
| P0-C4 | 若需調權，先產出 `docs/research/confidence-calibration-proposals/YYYY-MM-DD.md`，人工審核後才可改 `confidence_scorer.py`       |
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

## 7. 跨階段文件與 SOP

| 主題                | 文件                                               |
| ------------------- | -------------------------------------------------- |
| 信心分數調權流程    | `docs/development-execution-playbook.md`           |
| 策略版本遞增 SOP    | `docs/development-execution-playbook.md`           |
| 回測執行與結果解讀  | 本文件 P0 / P1 + `docs/research/backtest-results/` |
| LLM prompt 品質監控 | 本文件 P2 + `backend/eval-results/`                |

---

## 8. 文件維護規則

- 新增階段需求時，優先加入本文件，不再新增 `p0-*` / `p1-*` 形式的獨立 spec。
- 若某階段需求成為長期系統事實，移入對應長期 spec，並在本文件保留簡短決策紀錄。
- 被否決或暫緩需求不可刪除決策理由；應保留在「決策」段落，避免重複討論。
- 合併文件只保留可執行需求與驗收條件，過程討論與開放探索留在 `docs/plans/` 或 agent 對話中。
