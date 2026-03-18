# P0：新倉策略回測與校準 Implementation Plan

> 類型：Implementation Plan
> 建立日期：2026-03-18
> 狀態：**部分完成（Task 1/2 已完成；Task 3 待樣本累積後繼續）**
> 對應 Roadmap：`docs/research/post-new-position-strategy-optimization-roadmap.md` §5.1
> 前置依賴：`docs/plans/2026-03-18-p0-prerequisite.md` 全部完成
>
> ⚠️ **待辦提醒**：當 `analysis_is_final=TRUE` 記錄 >= 30 筆時，需回來執行 Task 3（初始基線回測），並補寫 `backend/backtest-results/README.md` 的回測紀錄表。

---

## 1. 背景

新倉策略演算法升級（`strategy_generator.py`）已完成，輸出 `strategy_type`（`short_term` / `mid_term` / `defensive_wait`）、`conviction_level`（`low` / `medium` / `high`）、完整 `action_plan` 結構。

然而，策略邏輯目前只能說「看起來合理」，尚無歷史資料能回答：

- `mid_term` 的勝率是否真的高於 `short_term`？
- `high` conviction 的實際表現是否優於 `medium`？
- 不同 evidence_score 分段的勝率如何分布？

本計劃的目標是利用 P0 前置已建立的回測腳本，對上述問題進行第一輪量化驗證。

---

## 2. 前提確認（開始前檢查）

1. `DailyAnalysisLog.strategy_version` 欄位已存在（P0 前置 Task 1）
2. `backtest_win_rate.py --mode new-position` 可執行（P0 前置 Task 2）
3. `DailyAnalysisLog` 中有 `strategy_type` 不為 NULL 的 `analysis_is_final = TRUE` 記錄

若第 3 點樣本數 < 30，應先累積資料再執行校準，本計劃的腳本擴充部分仍可照做。

---

## 3. 目標

1. 擴充回測腳本，支援依 `strategy_type`、`conviction_level`、`evidence_scores.total` 分箱統計
2. 支援多持有週期（5 / 10 / 20 日）同時輸出
3. 輸出結構化 JSON 報告，供人工審核與信心分數校準使用
4. 建立回測執行規範（何時跑、如何解讀、何時升級策略）

---

## 4. 範圍與非目標

**範圍內：**

- `backtest_win_rate.py` 新倉模式分箱統計擴充
- 回測 JSON 報告格式定義
- 回測執行一次並記錄初始基線結果
- 回測解讀準則文件（inline comment 或 README）

**範圍外：**

- 自動調整策略邏輯或權重（人工審核後才做）
- 前端回測報告頁面（P2+）
- 跨 market regime 分析（需更多樣本，P0 主體後期補充）

---

## 5. 指標定義（已在 P0 前置統一，此處確認）

| 指標 | 定義 |
|---|---|
| 樣本 | `analysis_is_final = TRUE`，`strategy_type IN ('short_term', 'mid_term')` |
| 持有週期 | 5 / 10 / 20 交易日（三組） |
| 勝率 | 第 N 個交易日收盤相對訊號日收盤漲幅 > +3% |
| 平手 | 漲跌幅介於 -3% ~ +3%，單獨列出，不計入勝/敗 |
| 報酬 | 絕對報酬 `(pN - p0) / p0 * 100` |

---

## 6. 實作步驟

### Task 1：回測腳本分箱統計擴充

目前 `--mode new-position` 只有整體勝率。需補以下分箱：

**6.1.1 `strategy_type` 分箱**

輸出各 strategy_type 的樣本數、勝率、平均報酬：

```
[short_term]  n=XX  勝率=XX%  平均報酬=XX%
[mid_term]    n=XX  勝率=XX%  平均報酬=XX%
```

**6.1.2 `conviction_level` 分箱**

```
[high]    n=XX  勝率=XX%  平均報酬=XX%
[medium]  n=XX  勝率=XX%  平均報酬=XX%
[low]     n=XX  勝率=XX%  平均報酬=XX%
```

**6.1.3 `evidence_scores.total` 分箱**

`evidence_scores` 存在 `DailyAnalysisLog.indicators` JSONB 欄位中，需從中取出 `evidence_scores.total`：

```
[total < 2]   n=XX  勝率=XX%
[total 2-3]   n=XX  勝率=XX%
[total 4+]    n=XX  勝率=XX%
```

**6.1.4 多持有週期同時輸出**

將現有單一 `--hold-days` 改為預設同時計算 5 / 10 / 20 日三組，並以矩陣格式輸出：

```
              5日勝率   10日勝率   20日勝率
[mid_term]     XX%       XX%        XX%
[short_term]   XX%       XX%        XX%
```

**受影響位置：**

- `scripts/backtest_win_rate.py`：新增 `strategy_type_stats()`、`conviction_stats()`、`evidence_score_stats()` 函式，`main()` 中 new-position 模式呼叫
- `DailyAnalysisLog.indicators` 中的 `evidence_scores.total` 需從 JSONB 取出（已存在，確認 key 路徑）

**驗收：**

- `python scripts/backtest_win_rate.py --mode new-position --days 90` 輸出含三組分箱的報告
- `--output-json` 輸出包含 `strategy_type_stats`、`conviction_stats`、`evidence_score_stats` 欄位

---

### Task 2：Pearson 相關性分析擴充（新倉版）

現有腳本的 Pearson 分析針對 Exit/Trim 訊號的 `signal_confidence` vs 下跌。新倉版需分析：

- `signal_confidence` vs 5日漲幅（是否正相關）
- `evidence_scores.total` vs 5日漲幅（是否正相關）

樣本門檻同現有邏輯：`< 5` 筆則跳過並提示。

**驗收：**

- 新倉模式輸出 `signal_confidence` Pearson r 與 p-value
- 若 `|r| < 0.2` 輸出警告提示

---

### Task 3：執行初始基線回測並記錄

前置與腳本擴充完成後，執行一次正式回測並保存結果：

```bash
cd backend
python scripts/backtest_win_rate.py \
  --mode new-position \
  --days 90 \
  --require-final-raw-data \
  --output-json backtest-results/new-position-baseline-$(date +%Y%m%d).json
```

結果存入 `docs/research/backtest-results/`（新建目錄）。

**需人工記錄的欄位：**

| 欄位 | 說明 |
|---|---|
| 執行日期 | 回測當天日期 |
| 樣本數 | 各分箱的 n |
| 整體勝率 | 5 / 10 / 20 日 |
| mid_term 勝率 | 5 / 10 / 20 日 |
| short_term 勝率 | 5 / 10 / 20 日 |
| high conviction 勝率 | 5 / 10 / 20 日 |
| signal_confidence Pearson r | 含 p-value |
| 備注 | 異常樣本、資料品質問題 |

---

## 7. 回測解讀準則

以下準則作為 inline comment 補充在腳本輸出末尾，供人工審核參考：

**勝率解讀：**

- 勝率 > 60%：策略在此分箱有預測價值，可信
- 勝率 50–60%：邊際有效，觀察更多樣本
- 勝率 < 50%：策略邏輯可能有問題，需人工審核

**分箱比較：**

- `mid_term` 勝率應顯著高於 `short_term`（否則兩類策略無區分意義）
- `high` conviction 勝率應高於 `medium` > `low`（否則 conviction 計算需重新校準）
- `evidence_scores.total >= 4` 的勝率應高於 `total < 2`

**樣本數要求：**

- 各分箱 n < 10：數據不足，結論不可靠，需持續累積
- 各分箱 n >= 30：可初步得出結論

---

## 8. 測試計劃

| 測試 | 方法 |
|---|---|
| 分箱統計輸出 | `--mode new-position --days 90` 輸出含三組分箱 |
| 多持有週期 | 輸出 5/10/20 日矩陣 |
| JSON 輸出完整性 | `--output-json` 輸出包含所有新欄位 |
| 向後相容 | `--mode position` 行為不變 |
| 樣本數不足處理 | 各分箱 n=0 時不報錯，輸出 `null` 或提示 |

---

## 9. 依賴與風險

| 項目 | 說明 |
|---|---|
| `indicators` JSONB 結構 | `evidence_scores.total` 需從 `DailyAnalysisLog.indicators` 中取出，若欄位結構不一致（舊資料）需容錯處理 |
| 樣本數不足 | 初期可能樣本數偏少，結論僅供參考，需標注 |
| 新倉策略演算法升級 | 若升級仍在進行，回測樣本需確認使用 `strategy_version` 篩選，避免混入舊邏輯的資料 |

---

## 10. 驗收定義

1. `--mode new-position` 輸出 strategy_type / conviction_level / evidence_score 三組分箱統計
2. `--output-json` 包含完整結構化結果
3. 初始基線回測 JSON 已存入 `docs/research/backtest-results/`
4. `--mode position` 行為不變

---

## 11. Spec Review

對應需求規格：`docs/p0-backtest-new-position-spec.md`

實作前請確認 spec 中以下項目無歧義：

- F2（指標定義表）：勝率門檻 +3%、平手區間、樣本限制是否已統一理解
- F3-3 / F4-3：`conviction_level` 與 `evidence_scores.total` 從 `indicators` JSONB 的取值路徑
- F4-4：多持有週期矩陣中「樣本不足警告」的觸發門檻（目前定為 < 5）
- F6-1 ~ F6-6：JSON 輸出的 key 命名，與信心校準計劃中的 baseline 格式是否一致

驗收時對照 spec AC1 ~ AC8 逐條確認。
