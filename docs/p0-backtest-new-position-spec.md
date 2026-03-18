# P0：新倉策略回測與校準 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-18-p0-backtest-new-position.md`
> 對應 Roadmap：§5.1
> 前置依賴：`docs/specs/p0-prerequisite-spec.md` 全部驗收條件通過

---

## 1. 背景問題

新倉策略演算法（`strategy_generator.py`）已基於 evidence-based scoring 完成設計，輸出 `strategy_type`、`conviction_level`、完整 `action_plan`。

然而目前無法回答以下問題：

- `mid_term` 的 5日/10日/20日勝率是否真的高於 `short_term`？
- `conviction_level = high` 的實際表現是否優於 `medium`？
- `evidence_scores.total` 較高的分組，勝率是否顯著不同？
- `signal_confidence` 與後續漲幅是否存在統計上的正相關？

若無法回答上述問題，策略輸出只是「看起來合理」，缺乏客觀依據。

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 回測腳本支援按 `strategy_type` / `conviction_level` / `evidence_scores.total` 三組分箱統計 |
| G2 | 支援 5 / 10 / 20 日三個持有週期同時輸出 |
| G3 | 輸出結構化 JSON 報告，供人工審核與後續信心校準使用 |
| G4 | 完成第一次基線回測並保存結果，建立可比較的初始基準 |

---

## 3. 範圍

### 範圍內

- `backtest_win_rate.py` 新倉模式新增三組分箱統計
- 多持有週期矩陣輸出（5 / 10 / 20 日）
- `signal_confidence` vs 漲幅的 Pearson 相關性分析（新倉版）
- `evidence_scores.total` vs 漲幅的 Pearson 相關性分析
- 執行初始基線回測，結果存入 `docs/research/backtest-results/`

### 範圍外

- 自動調整策略邏輯或權重（需人工審核，屬信心校準計劃）
- 前端回測報告頁面（P2+）
- 跨 market regime 分析（需更多樣本）
- 持股診斷 Exit/Trim 回測的任何修改

---

## 4. 功能需求

### F1：strategy_type 分箱統計

| 編號 | 需求 |
|---|---|
| F1-1 | 新倉模式輸出各 `strategy_type` 分組的樣本數、勝率、平局率、敗率、平均報酬 |
| F1-2 | 分組為：`short_term`、`mid_term` |
| F1-3 | 各組輸出格式一致，n=0 時顯示「-」或 `null`，不報錯 |

### F2：conviction_level 分箱統計

| 編號 | 需求 |
|---|---|
| F2-1 | 新倉模式輸出各 `conviction_level` 分組的樣本數、勝率、平均報酬 |
| F2-2 | 分組為：`high`、`medium`、`low` |
| F2-3 | `conviction_level` 從 `DailyAnalysisLog.indicators` JSONB 中取出 |
| F2-4 | 若 `conviction_level` 欄位不存在（舊記錄），該筆跳過並計入 skip 統計 |

### F3：evidence_scores.total 分箱統計

| 編號 | 需求 |
|---|---|
| F3-1 | 新倉模式輸出各 `evidence_scores.total` 分組的樣本數、勝率 |
| F3-2 | 分組為：`< 2`、`2–3`、`>= 4` |
| F3-3 | `evidence_scores.total` 從 `DailyAnalysisLog.indicators` JSONB 中取出 |
| F3-4 | 若 `evidence_scores` 欄位不存在（舊記錄），該筆跳過並計入 skip 統計 |

### F4：多持有週期矩陣輸出

| 編號 | 需求 |
|---|---|
| F4-1 | 新倉模式預設同時計算 5 / 10 / 20 日三組勝率 |
| F4-2 | 輸出格式為矩陣，行為 `strategy_type`，列為持有週期 |
| F4-3 | `--hold-days` 仍可指定單一週期（此時只輸出該週期） |
| F4-4 | 矩陣中各格若樣本不足（< 5）需標注警告 |

### F5：Pearson 相關性分析（新倉版）

| 編號 | 需求 |
|---|---|
| F5-1 | 輸出 `signal_confidence` vs 5日漲幅的 Pearson r 與 p-value |
| F5-2 | 輸出 `evidence_scores.total` vs 5日漲幅的 Pearson r 與 p-value |
| F5-3 | 有效樣本 < 5 時跳過分析並輸出提示 |
| F5-4 | `|r| < 0.2` 時輸出警告：「相關性偏低，建議人工審核」 |
| F5-5 | `|r| >= 0.2` 時輸出：「相關性合理」 |

### F6：JSON 輸出

| 編號 | 需求 |
|---|---|
| F6-1 | `--output-json` 輸出包含以下頂層 key：`days`、`mode`、`hold_days`、`data_quality`、`strategy_type_stats`、`conviction_stats`、`evidence_score_stats`、`multi_period_matrix`、`pearson` |
| F6-2 | `strategy_type_stats`：各分組的 `n`、`win_rate`、`draw_rate`、`loss_rate`、`avg_return` |
| F6-3 | `conviction_stats`：各分組的 `n`、`win_rate`、`avg_return` |
| F6-4 | `evidence_score_stats`：各分組的 `n`、`win_rate` |
| F6-5 | `multi_period_matrix`：二維結構，`{strategy_type: {hold_days: win_rate}}` |
| F6-6 | `pearson`：`{signal_confidence: {r, p, n}, evidence_total: {r, p, n}}` |

### F7：基線回測執行

| 編號 | 需求 |
|---|---|
| F7-1 | 首次正式回測使用 `--require-final-raw-data` 確保樣本品質 |
| F7-2 | 結果存入 `docs/research/backtest-results/new-position-baseline-YYYYMMDD.json` |
| F7-3 | 同目錄建立 `README.md`，記錄每次回測的執行日期、主要發現、`strategy_version` |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | `--mode position` 的輸出結果不受任何影響 |
| NF2 | 各分箱 n=0 時腳本不報錯，輸出 `null` 或明確提示 |
| NF3 | JSONB 欄位中 `indicators` 結構若不符預期（舊格式），容錯跳過，不中斷整批回測 |

---

## 6. 回測解讀準則（正式定義）

以下準則為本系統採用的標準解讀基準，納入 spec 作為日後審核依據：

### 勝率判斷

| 勝率範圍 | 解讀 |
|---|---|
| > 60% | 策略在此分箱有預測價值 |
| 50–60% | 邊際有效，需持續累積樣本觀察 |
| < 50% | 策略邏輯可能有問題，需人工審核 |

### 分箱一致性預期

| 預期 | 說明 |
|---|---|
| `mid_term` 勝率 > `short_term` | 否則兩類策略無區分意義 |
| `high` > `medium` > `low` conviction 勝率 | 否則 conviction 計算需重新校準 |
| `evidence_scores.total >= 4` 勝率 > `total < 2` | 否則 evidence scoring 無預測力 |

### 樣本數要求

| 樣本數 | 可靠度 |
|---|---|
| < 10 | 不可靠，僅供觀察 |
| 10–29 | 初步參考，謹慎解讀 |
| >= 30 | 可初步得出結論 |

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | `--mode new-position` 輸出含 `strategy_type` / `conviction_level` / `evidence_scores` 三組分箱 | CLI 執行 |
| AC2 | 多持有週期矩陣（5/10/20日）正確輸出 | CLI 執行 |
| AC3 | `--output-json` 包含 F6 定義的所有頂層 key | 驗證 JSON |
| AC4 | `signal_confidence` 與 `evidence_scores.total` Pearson 分析均有輸出 | CLI 執行 |
| AC5 | 樣本數為 0 的分箱不報錯 | CLI 執行（`--days 1`） |
| AC6 | `--mode position`（或無 `--mode`）輸出與修改前完全一致 | CLI 執行對比 |
| AC7 | 初始基線回測 JSON 已存入 `docs/research/backtest-results/` | 確認檔案存在 |
| AC8 | `docs/research/backtest-results/README.md` 記錄第一次回測的主要發現 | 確認內容 |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| P0 前置全部完成 | `strategy_version` 欄位已存在、`--mode new-position` 基礎架構已建立 |
| `DailyAnalysisLog.indicators` 包含 `conviction_level` | 確認 `strategy_generator.py` 寫入 indicators 的結構 |
| `DailyAnalysisLog.indicators` 包含 `evidence_scores.total` | 同上 |
| 有效樣本存在 | 若樣本數過少，AC7/AC8 基線回測仍可執行，但解讀需標注「樣本不足」 |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| `indicators` JSONB 的 `conviction_level` 存放路徑確認 | 待實作前確認 `action_plan.conviction_level` 是否有寫入 `indicators` |
| 多持有週期矩陣是否需要合併成單一 CLI 輸出還是分段輸出 | 待開發時決定，不影響 JSON 輸出格式 |
