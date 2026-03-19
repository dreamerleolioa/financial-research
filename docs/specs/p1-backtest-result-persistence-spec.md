# P1：回測結果持久化 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-19-p1-implementations.md`
> 對應 Roadmap：§5.4b

---

## 1. 背景問題

P0 的回測腳本（`backtest_win_rate.py`）目前以 JSON 檔輸出至 `backend/backtest-results/`。這是允許快速迭代的過渡做法，但有以下問題：

1. **歷史回測結果無法查詢**：每次執行覆蓋或新增 JSON 檔，無法系統性地比較不同時間點的回測結果
2. **與策略版本無關聯**：JSON 檔沒有 `strategy_version` 標記，P0 前置雖已加入版本號，但 JSON 輸出未對應利用
3. **無法做趨勢分析**：無法查詢「過去 30 天每週的勝率變化」這類問題
4. **`backtest-results/` 目錄是臨時垃圾桶**：應在持久化完成後移除，避免混淆

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 設計 `backtest_run` 與 `backtest_result` DB schema，每次執行產生一筆 run 記錄，每個股票一筆 result 記錄 |
| G2 | `backtest_win_rate.py` 的 `--output-json` 邏輯改為寫入 DB |
| G3 | 移除 `backend/backtest-results/` 目錄 |
| G4 | 保留 CLI 輸出（console 印出勝率摘要），不影響可讀性 |

---

## 3. 範圍

### 範圍內

- 新增 `BacktestRun` 與 `BacktestResult` DB 模型
- 新增對應 Alembic migration
- `backtest_win_rate.py`：寫入 DB 取代 `--output-json` 的 JSON 寫檔行為
- 移除 `backend/backtest-results/` 目錄（及 `.gitignore` 相關規則）

### 範圍外

- 回測報表前端頁面（P2+）
- 自動定期執行回測（P2+，cron job）
- 回測結果的 API 查詢端點（P2+）
- 舊有 JSON 檔資料遷移（不做，歷史 JSON 直接捨棄）

---

## 4. 資料模型定義

### `backtest_run`（每次執行一筆）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | `INTEGER` PK | |
| `run_date` | `DATE` | 執行日期（UTC） |
| `mode` | `VARCHAR(20)` | `"position"` 或 `"new-position"` |
| `hold_days` | `INTEGER` | 持有天數（5 / 10 / 20） |
| `days_lookback` | `INTEGER` | 回測往回查幾天（--days 參數） |
| `strategy_version` | `VARCHAR(20)` | 執行時的 `STRATEGY_VERSION` 常數值 |
| `total_samples` | `INTEGER` | 本次回測的總樣本數 |
| `win_count` | `INTEGER` | 勝場數 |
| `loss_count` | `INTEGER` | 敗場數 |
| `draw_count` | `INTEGER` | 平手數 |
| `skip_count` | `INTEGER` | 跳過數（無後續價格資料） |
| `win_rate` | `NUMERIC(5,2)` | 總勝率（%） |
| `created_at` | `TIMESTAMP` | |

### `backtest_result`（每個股票樣本一筆）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | `INTEGER` PK | |
| `run_id` | `INTEGER` FK → `backtest_run.id` | |
| `symbol` | `VARCHAR(20)` | 股票代號 |
| `signal_date` | `DATE` | 訊號發出日（DailyAnalysisLog.record_date） |
| `p0_price` | `NUMERIC(12,4)` | 訊號日收盤價 |
| `pN_price` | `NUMERIC(12,4)` | 第 N 個交易日收盤價（N = hold_days） |
| `pct_change` | `NUMERIC(8,4)` | `(pN - p0) / p0 * 100` |
| `outcome` | `VARCHAR(10)` | `"win"` / `"loss"` / `"draw"` / `"skip"` |
| `skip_reason` | `TEXT` nullable | 跳過原因（如「無後續價格資料」） |
| `signal_confidence` | `NUMERIC(5,2)` | 分析時的信心分數 |
| `conviction_level` | `VARCHAR(10)` | `"low"` / `"medium"` / `"high"` |
| `strategy_type` | `VARCHAR(30)` | `"short_term"` / `"mid_term"` / `"defensive_wait"` |
| `action_tag` | `VARCHAR(20)` | `"opportunity"` / `"overheated"` / `"neutral"` |
| `log_id` | `INTEGER` FK → `daily_analysis_log.id` nullable | 原始 log 記錄 |

---

## 5. 功能需求

### F1：DB Schema 與 Migration

| 編號 | 需求 |
|---|---|
| F1-1 | 新增 `BacktestRun` 與 `BacktestResult` ORM 模型 |
| F1-2 | Alembic migration 可正常 upgrade / downgrade |
| F1-3 | `backtest_result.run_id` 有外鍵關聯到 `backtest_run.id`，cascade delete |

### F2：回測腳本寫入 DB

| 編號 | 需求 |
|---|---|
| F2-1 | `backtest_win_rate.py` 執行結束後，自動寫入一筆 `BacktestRun` 記錄 |
| F2-2 | 每個樣本的計算結果寫入一筆 `BacktestResult` 記錄 |
| F2-3 | `--output-json` 參數移除（或保留但標記 deprecated，不再寫檔） |
| F2-4 | CLI console 摘要輸出不變（勝率、樣本數等仍印出） |
| F2-5 | DB 寫入失敗時腳本不靜默失敗，輸出明確錯誤訊息 |
| F2-6 | `strategy_version` 從 `config.STRATEGY_VERSION` 讀取並記錄至 `backtest_run` |

### F3：移除暫存目錄

| 編號 | 需求 |
|---|---|
| F3-1 | 移除 `backend/backtest-results/` 目錄及其內容 |
| F3-2 | 移除 `.gitignore` 中對應的 `backtest-results/` 規則（若有） |

---

## 6. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | 腳本執行時間不因 DB 寫入而顯著增加（寫入為批次，非逐筆提交） |
| NF2 | 若無 DB 連線（如本機未啟動 DB），腳本輸出明確錯誤而非掛起 |
| NF3 | `BacktestRun` 與 `BacktestResult` 資料表不影響現有 API 路由與快取邏輯 |

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | `alembic upgrade head` 後 `backtest_run` 與 `backtest_result` 資料表存在 | 查 DB |
| AC2 | `alembic downgrade -1` 後兩表消失，`alembic upgrade head` 後恢復 | 手動執行 |
| AC3 | 執行 `python scripts/backtest_win_rate.py --mode new-position --days 30` 後，`backtest_run` 有一筆新記錄 | 查 DB |
| AC4 | 對應的 `backtest_result` 記錄數與 console 印出的樣本數一致 | 查 DB + console |
| AC5 | `backend/backtest-results/` 目錄已移除 | ls 確認 |
| AC6 | `strategy_version` 欄位值與 `config.STRATEGY_VERSION` 一致 | 查 DB |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| P0 前置完成 | `backtest_win_rate.py` 已支援 `--mode new-position`，`strategy_version` 已存在 |
| P0 回測腳本輸出格式穩定 | schema 定義不再大幅變動，才適合固化至 DB |
| Alembic 環境正常 | 參考 `docs/plans/2026-03-11-alembic-migration.md` |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| 同一天執行兩次回測是否允許（`run_date` 重複）？ | 決定：允許，`run_date` 無 unique 約束，每次執行都是獨立記錄 |
| `--output-json` 是否保留為可選輸出？ | 決定：移除，DB 為單一來源；若需要 JSON 可後續從 DB export |
