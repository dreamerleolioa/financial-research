# 勝率回測腳本優化 Implementation Plan

> 狀態：待執行
> 預計執行日期：2026-03-17

**Goal:** 將現有勝率回測腳本升級為符合雙旗標語義的回測工具，明確區分 final raw data 與 final analysis，避免把盤中/未定稿分析混入歷史勝率統計。

**Current Gap:** 現行 [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py) 直接讀取 `daily_analysis_log` 並以 yfinance 抓取訊號日與 5 日後收盤價，但目前沒有：

- 過濾 `analysis_is_final = TRUE`
- 對 `raw_data_is_final = TRUE` 的原始資料做優先使用
- 區分「分析訊號準確率」與「價格資料完備度」
- 輸出資料品質統計，說明哪些樣本因未定稿或缺資料被排除

**Architecture:** 依 review-spec v2.0，勝率回測應拆成兩個判斷層次：

- 訊號樣本有效性：只納入 `analysis_is_final = TRUE` 的診斷紀錄，確保 action tag / confidence 來自定稿分析
- 價格與指標資料有效性：優先依賴 `stock_raw_data.raw_data_is_final = TRUE` 的原始資料；若腳本暫時仍使用 yfinance 作為價格來源，至少必須在統計上區分「分析定稿」與「raw data 定稿」的樣本數

本次優化仍維持 CLI 腳本型式，不先改成 API 或 n8n Workflow。

---

## Task 1: 釐清回測口徑與資料範圍

**Files:**

- Modify: [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py)
- Optional Reference: [docs/specs/ai-stock-sentinel-automation-review-spec.md](docs/specs/ai-stock-sentinel-automation-review-spec.md)

**目標決策**

1. 回測主口徑定義為「分析訊號準確率」，不是單純價格統計。
2. 樣本必須滿足 `daily_analysis_log.analysis_is_final = TRUE`。
3. 若未來要做純價格/技術條件回測，應另外拆成第二支腳本，不與本腳本混用。

**預期結果**

- 腳本預設只統計 final analysis 樣本
- 報表中明確標示排除的未定稿樣本數

---

## Task 2: 重構 DB 讀取與樣本過濾

**Files:**

- Modify: [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py)

**Step 1: 擴充查詢條件**

- `fetch_logs()` 新增以下過濾能力：
  - `analysis_is_final_only: bool = True`
  - `symbols: list[str] | None = None`
  - `min_confidence: float | None = None`

**Step 2: 預設只讀 final analysis**

範例方向：

```python
q = db.query(DailyAnalysisLog).filter(DailyAnalysisLog.record_date >= since)
q = q.filter(DailyAnalysisLog.analysis_is_final.is_(True))
```

**Step 3: 補資料品質統計**

至少輸出：

- 總 log 筆數
- `analysis_is_final = TRUE` 納入筆數
- 因未定稿被排除筆數
- 因價格資料不足被跳過筆數

**預期結果**

- 腳本輸出不再把盤中或未定稿分析混入勝率計算
- 之後校準 confidence 時，樣本品質較可解釋

---

## Task 3: 引入 raw data finalize 感知

**Files:**

- Modify: [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py)
- Modify: [backend/src/ai_stock_sentinel/db/models.py](backend/src/ai_stock_sentinel/db/models.py) 若腳本需要引用 `StockRawData`

**目標**

即使短期價格來源仍來自 yfinance，腳本也要知道該訊號日是否存在 `stock_raw_data.raw_data_is_final = TRUE` 的紀錄，避免未來誤把資料完整性與分析完整性混為一談。

**Step 1: 建立 raw data lookup**

- 以 `(symbol, record_date)` 查 `stock_raw_data`
- 判斷是否存在 `raw_data_is_final = TRUE`

**Step 2: 報表新增欄位**

至少輸出：

- 具有 final raw data 的樣本數
- 沒有 final raw data 的樣本數
- 若某些樣本只有 final analysis、沒有 final raw data，顯示 warning

**Step 3: CLI 開關**

新增參數：

- `--require-final-raw-data`

效果：

- 開啟時，只納入同時滿足 `analysis_is_final = TRUE` 且 `raw_data_is_final = TRUE` 的樣本
- 關閉時，仍輸出 final raw data 覆蓋率，供人工判斷

---

## Task 4: 優化價格取得與效能

**Files:**

- Modify: [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py)

**目前問題**

- 每筆 log 都分別呼叫兩次 yfinance
- 同一個 symbol 可能被重複抓多次
- 大區間回測容易慢且不穩

**優化方向**

1. 以 `(symbol, signal_date)` 做記憶體快取
2. 改成單次抓較長區間歷史價格，再在本地切第 0 天與第 5 個交易日
3. 對失敗的 symbol 加入錯誤統計，不要只有 skipped

**預期結果**

- 90 天以上回測執行時間明顯下降
- `skipped` 的原因可追蹤，不會混成單一數字

---

## Task 5: 強化輸出報表

**Files:**

- Modify: [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py)

**新增輸出項目**

- 每個 `action_tag` 的樣本數、排除數、勝率
- final analysis 覆蓋率
- final raw data 覆蓋率
- Pearson 相關性使用樣本數
- 置信度區間分桶統計，例如：`<60`、`60-70`、`70-80`、`80+`

**可選輸出**

- `--output-json <path>`：輸出結構化結果，方便未來接 n8n 或 dashboard

---

## Task 6: 測試與驗證

**Files:**

- Create: `backend/tests/test_backtest_win_rate.py`
- Modify: [backend/scripts/backtest_win_rate.py](backend/scripts/backtest_win_rate.py)

**最少測試案例**

1. `analysis_is_final = FALSE` 的樣本不應被納入預設回測
2. `--require-final-raw-data` 開啟時，缺少 final raw data 的樣本應被排除
3. 價格資料不足時應被正確計入 `skipped`
4. Pearson 樣本不足時應正確跳過，不報錯

**執行命令**

```bash
cd backend && pytest tests/test_backtest_win_rate.py -v
cd backend && python scripts/backtest_win_rate.py --days 90
cd backend && python scripts/backtest_win_rate.py --days 90 --require-final-raw-data
```

---

## Task 7: 明日執行順序

1. 先改 ORM / script 查詢欄位，支援 `analysis_is_final` 與 `raw_data_is_final`
2. 再補 CLI 參數與報表輸出
3. 然後做價格抓取快取，避免測試時過慢
4. 最後補測試與手動驗證

**完成標準**

- 腳本預設只使用 `analysis_is_final = TRUE` 樣本
- 可選擇只使用 `raw_data_is_final = TRUE` 樣本
- 回測報表能顯示資料品質覆蓋率
- 不再把未定稿分析與定稿分析混算

---

_文件版本：v1.0 | 建立日期：2026-03-16 | 預計執行：2026-03-17 | 關聯檔案：`backend/scripts/backtest_win_rate.py`、`docs/specs/ai-stock-sentinel-automation-review-spec.md`_
