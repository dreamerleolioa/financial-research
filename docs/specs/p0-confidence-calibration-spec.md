# P0：信心分數校準 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-18-p0-confidence-calibration.md`
> 對應 Roadmap：§5.2
> 前置依賴：`docs/specs/p0-backtest-new-position-spec.md` 全部驗收條件通過，且各 confidence 分桶各有 >= 5 筆有效樣本

---

## 1. 背景問題

`confidence_scorer.py` 的 `signal_confidence` 以固定權重計算：

| 維度 | 訊號 | 當前權重 |
|---|---|---|
| inst_flow | `institutional_accumulation` | +7 |
| inst_flow | `distribution` | -10 |
| inst_flow | `retail_chasing` | -8 |
| sentiment | `positive` | +5 |
| sentiment | `negative` | -5 |
| technical | `bullish` | +5 |
| technical | `bearish` | -5 |
| 特殊情境 | 三維共振 bonus | +3 |
| 特殊情境 | 利多出貨 penalty | -7 |
| 資料品質 | `DATE_UNKNOWN` | -3 |

這些權重來自設計時的主觀判斷，從未經過歷史資料驗證。

**核心風險：若高 `signal_confidence` 不對應高勝率，高分反而傷害使用者對系統的信任感。**

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 驗證 `signal_confidence` 分桶與新倉策略實際勝率是否正相關（高分 → 高勝率） |
| G2 | 分析各維度（sentiment / inst_flow / technical）對新倉勝率的獨立貢獻 |
| G3 | 若發現問題，產出有文件依據的調權提案，經人工審核後才修改程式碼 |
| G4 | 建立可重複執行的半自動調權流程規範 |

---

## 3. 範圍

### 範圍內

- 信心分桶（`<60 / 60-70 / 70-80 / 80+`）vs 新倉勝率分析
- 各維度訊號分組 vs 新倉勝率分析（一次性分析腳本）
- 若問題明確：產出調權提案文件、人工審核、修改 `confidence_scorer.py`、更新 `STRATEGY_VERSION`、重跑回測驗證
- 調權流程規範文件化

### 範圍外

- 自動化調權（機器學習等）
- 生產環境即時信心分數調整
- 對持股診斷（`/analyze/position`）的 `signal_confidence` 做獨立校準（另立計劃）
- `data_confidence` 的校準（不在本計劃範圍）

---

## 4. 功能需求

### F1：信心分桶 vs 勝率分析

| 編號 | 需求 |
|---|---|
| F1-1 | 從回測結果取出各 confidence 分桶（`<60 / 60-70 / 70-80 / 80+`）的勝率（5日/10日） |
| F1-2 | 輸出各分桶的 n、勝率、平均報酬 |
| F1-3 | 若各分桶勝率不呈單調遞增，輸出警告並標注哪個分桶異常 |
| F1-4 | 各分桶 n < 5 時標注「樣本不足，結論不可靠」 |

### F2：維度貢獻分析腳本

| 編號 | 需求 |
|---|---|
| F2-1 | 建立 `scripts/analyze_confidence_breakdown.py`，為一次性分析工具 |
| F2-2 | 從 `DailyAnalysisLog.indicators` 取出各訊號維度，分組比較勝率 |
| F2-3 | 分組一：按 `inst_flow` 標籤（`institutional_accumulation` / `distribution` / `neutral`） |
| F2-4 | 分組二：按 `sentiment_label`（`positive` / `negative` / `neutral`） |
| F2-5 | 分組三：按 `technical_signal`（`bullish` / `bearish` / `sideways`） |
| F2-6 | 各分組輸出：n、5日勝率、平均報酬 |
| F2-7 | 支援 `--days` 與 `--output-json` 參數，格式與主腳本一致 |

### F3：調權提案文件（若需要）

| 編號 | 需求 |
|---|---|
| F3-1 | 若 F1 或 F2 發現問題，產出調權提案文件至 `docs/research/confidence-calibration-proposals/YYYY-MM-DD.md` |
| F3-2 | 提案文件必須包含：問題描述、診斷依據（附回測數據）、具體建議變更（含前後值）、預期效果、風險說明 |
| F3-3 | 提案未經人工審核前，不得修改 `confidence_scorer.py` |

### F4：調權執行（審核通過後）

| 編號 | 需求 |
|---|---|
| F4-1 | 只修改 `confidence_scorer.py` 中已在提案中明確列出的常數 |
| F4-2 | 修改後同步更新 `STRATEGY_VERSION`（minor version bump，如 `1.0.0` → `1.1.0`） |
| F4-3 | 修改後重新執行完整回測，結果存入 `docs/research/backtest-results/new-position-post-calibration-YYYYMMDD.json` |
| F4-4 | post-calibration 回測的高分桶勝率 >= baseline 同分桶勝率（若無改善需說明） |

### F5：調權流程規範文件化

| 編號 | 需求 |
|---|---|
| F5-1 | 在 `docs/development-execution-playbook.md` 補充「信心分數調權流程」章節 |
| F5-2 | 流程包含：執行回測 → 分析 → 提案 → 審核 → 修改 → 版本遞增 → 驗證 → 若無改善回滾 |
| F5-3 | 明確標注：每次調整需間隔至少一個月的新樣本，避免 overfitting |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | `confidence_scorer.py` 修改前後，所有現有單元測試仍須通過 |
| NF2 | 權重調整不得改變函式簽名或輸出 key 結構（只改常數值） |
| NF3 | 調整 `confidence_scorer.py` 前必須確認是否影響持股診斷端的語意（`/analyze/position` 同樣使用此 scorer） |
| NF4 | 分析腳本不寫入 DB，只讀取資料與輸出報告 |

---

## 6. 診斷矩陣（Spec 層級正式定義）

以下為判斷是否需要調權的標準，作為 spec review 的依據：

### 需要調權的情況

| 現象 | 診斷方向 |
|---|---|
| 高分桶（80+）勝率低於低分桶（<60） | 高分條件（如三維共振 bonus）可能不具備新倉預測力 |
| 各分桶勝率無統計差異（< 5% 差距） | 整體 confidence 計算對新倉無區分力，需重新設計 |
| `distribution` 分組勝率高於 `institutional_accumulation` | `inst_flow` 的方向性設計可能有問題 |
| Pearson r < 0.1 且 p > 0.1 | confidence 與漲幅完全無相關，需徹底重新評估 |

### 不需要調權（可接受）的情況

| 現象 | 解讀 |
|---|---|
| 各分桶趨勢正確但差距不大（10-20%） | 正常，訊號本身有噪音 |
| 樣本數不足導致信心區間寬 | 等待更多樣本，暫不調整 |
| 某維度分析 p-value > 0.05 | 需更多樣本才能確認，不等同於需要調整 |

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | 信心分桶 vs 勝率分析報告已產出（即使結論是「無需調整」） | 查 backtest-results/ 或 CLI 輸出 |
| AC2 | `scripts/analyze_confidence_breakdown.py` 可執行，輸出三組維度分析 | CLI 執行 |
| AC3 | 若有調整：`confidence_scorer.py` 修改前有對應提案文件且標注已審核 | 查 confidence-calibration-proposals/ |
| AC4 | 若有調整：`STRATEGY_VERSION` 已從 `1.0.x` 遞增 | 查 `config.py` |
| AC5 | 若有調整：所有既有 `test_confidence_scorer.py` 測試仍通過 | 執行測試 |
| AC6 | 若有調整：post-calibration 回測 JSON 已存入 `docs/research/backtest-results/` | 確認檔案 |
| AC7 | `development-execution-playbook.md` 有「信心分數調權流程」章節 | 確認內容 |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| P0 回測完成且有基線結果 | 需有 `docs/research/backtest-results/new-position-baseline-*.json` |
| 各 confidence 分桶各有 >= 5 筆有效樣本 | 樣本不足時可執行分析但不應做調整 |
| `DailyAnalysisLog.indicators` 有 `sentiment_label`、`inst_flow`、`technical_signal` | 確認寫入 indicators 的結構 |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| `confidence_scorer.py` 的權重是否對持股診斷與新倉策略同等適用？ | 待分析後決定，若不同需考慮分開設計（長期）|
| 調整幅度的上限是否需要限制（如每次不超過 ±3）？ | 待第一次調整時討論，目前不設硬性限制 |
