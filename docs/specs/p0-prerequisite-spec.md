# P0 前置：回測地基建設 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-18-p0-prerequisite.md`
> 對應 Roadmap：§5.0 / §5.0b / §5.0c

---

## 1. 背景問題

P0 的兩項核心工作（新倉策略回測、信心分數校準）都依賴共同基礎設施，若在這些基礎到位前就啟動 P0，會有以下風險：

1. **回測腳本無新倉模式**：現有腳本只針對持股診斷的 Exit/Trim 訊號，無法量測新倉策略的預測力
2. **回測結果無版本標記**：無法確認某次回測對應的是哪版策略邏輯，日後比較無意義
3. **資料品質不可觀測**：若資料源有 fallback 問題，回測樣本本身就是髒的，校準結果無效

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 建立新倉策略回測的標準指標定義（勝率、報酬、持有週期） |
| G2 | `backtest_win_rate.py` 支援新倉策略回測模式，且現有模式不受影響 |
| G3 | 策略輸出與回測記錄帶有版本標記，未來可依版本切分比較 |
| G4 | 資料源每次請求的成功/失敗可從 log 追蹤，fallback 命中可識別 |

---

## 3. 範圍

### 範圍內

- `DailyAnalysisLog` / `StockAnalysisCache` 新增 `strategy_version` 欄位
- `config.py` 新增 `STRATEGY_VERSION` 常數（初始值 `"1.0.0"`）
- 每次分析結果寫入 DB 時同步寫入 `strategy_version`
- `backtest_win_rate.py` 新增 `--mode new-position` 模式
- 資料源 router / yfinance / rss 的請求結果改輸出結構化 JSON log

### 範圍外

- 完整策略版本化系統（P2 計劃）
- Provider health dashboard 或告警機制（P1 計劃）
- 信心分數校準（P0 主體計劃）
- 回測報表 UI（P2+）

---

## 4. 功能需求

### F1：策略版本號

| 編號 | 需求 |
|---|---|
| F1-1 | `config.py` 有 `STRATEGY_VERSION` 字串常數，初始值為 `"1.0.0"` |
| F1-2 | `DailyAnalysisLog` 有 `strategy_version` 欄位（`VARCHAR(20)`，nullable） |
| F1-3 | `StockAnalysisCache` 有 `strategy_version` 欄位（`VARCHAR(20)`，nullable） |
| F1-4 | 每次新分析完成後寫入 DB 時，`strategy_version` 填入當前 `STRATEGY_VERSION` 值 |
| F1-5 | 舊有記錄的 `strategy_version` 為 `NULL`，不做回填 |
| F1-6 | Alembic migration 可正常 upgrade / downgrade |

### F2：新倉回測指標定義

以下為統一定義，供腳本實作與人工解讀共同遵守：

| 指標 | 定義 |
|---|---|
| 樣本來源 | `DailyAnalysisLog.strategy_type IN ('short_term', 'mid_term')`，`analysis_is_final = TRUE` |
| 排除樣本 | `strategy_type = 'defensive_wait'`（觀望訊號不納入勝率計算） |
| 持有週期 | 5 / 10 / 20 交易日（三組獨立計算） |
| 勝率定義 | 訊號日起第 N 個交易日收盤相對訊號日收盤漲幅 > +3% |
| 敗率定義 | 漲幅 < -3% |
| 平手定義 | 漲幅介於 -3% ~ +3%（含端點），單獨列出，不計入勝或敗 |
| 報酬計算 | 絕對報酬：`(pN - p0) / p0 * 100` |
| 樣本限制 | 預設 `analysis_is_final = TRUE`；可加 `--require-final-raw-data` 進一步限制 |

### F3：回測腳本新倉模式

| 編號 | 需求 |
|---|---|
| F3-1 | `backtest_win_rate.py` 新增 `--mode` 參數，可接受 `position`（預設）或 `new-position` |
| F3-2 | `--mode position` 行為與現有完全一致（向後相容） |
| F3-3 | `--mode new-position` 以 F2 定義的指標計算勝率 |
| F3-4 | `--mode new-position` 支援 `--hold-days`（5 / 10 / 20），預設 5 |
| F3-5 | 樣本數為 0 時腳本不報錯，輸出「無符合條件的記錄」提示 |
| F3-6 | `--output-json` 在新倉模式下可正常輸出結構化結果 |

### F4：資料源結構化 logging

| 編號 | 需求 |
|---|---|
| F4-1 | `institutional_flow/router.py` 的每次 provider 命中輸出 `provider_success` JSON log |
| F4-2 | `institutional_flow/router.py` 的每次 provider 失敗輸出 `provider_failure` JSON log |
| F4-3 | `yfinance_client.py` 的成功/失敗路徑輸出對應結構化 log |
| F4-4 | `rss_news_client.py` 的成功/失敗路徑輸出對應結構化 log |
| F4-5 | `provider_success` log 包含欄位：`event`、`provider`、`symbol`、`is_fallback` |
| F4-6 | `provider_failure` log 包含欄位：`event`、`provider`、`symbol`、`error_code` |
| F4-7 | 可用 `grep provider_success` 或 `grep provider_failure` 從 log 做統計 |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | `--mode position` 的輸出結果與修改前完全一致（不可有行為回歸） |
| NF2 | `strategy_version` 欄位新增不影響既有 API response 結構 |
| NF3 | 結構化 log 不影響現有 logging 的 level 設定（info/warning 對應不變） |
| NF4 | Migration 可在 Render 生產環境正常執行 |

---

## 6. 資料欄位定義

### `DailyAnalysisLog.strategy_version`

| 屬性 | 值 |
|---|---|
| 型別 | `VARCHAR(20)` |
| nullable | 是（舊資料為 NULL） |
| 範例值 | `"1.0.0"` |
| 更新時機 | 每次分析結果寫入時，填入當前 `STRATEGY_VERSION` |

### provider log 事件格式

```json
// 成功
{
  "event": "provider_success",
  "provider": "twse-openapi",
  "symbol": "2330.TW",
  "is_fallback": true
}

// 失敗
{
  "event": "provider_failure",
  "provider": "finmind",
  "symbol": "2330.TW",
  "error_code": "rate_limit"
}
```

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | 執行 `/analyze` 後，`DailyAnalysisLog` 新記錄的 `strategy_version` = `"1.0.0"` | 查 DB |
| AC2 | `alembic downgrade -1` 後 `strategy_version` 欄位消失，`alembic upgrade head` 後恢復 | 手動執行 |
| AC3 | `python scripts/backtest_win_rate.py --mode new-position --days 30` 可執行，不報錯 | CLI 執行 |
| AC4 | `python scripts/backtest_win_rate.py --days 30`（無 `--mode`）輸出與修改前一致 | CLI 執行 |
| AC5 | 執行 `/analyze` 後，log 中可見含 `"event": "provider_success"` 的 JSON 行 | grep log |
| AC6 | 所有新功能均有對應單元測試或手動驗證記錄 | 測試 / 記錄 |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| Alembic 環境正常 | 參考 `docs/plans/2026-03-11-alembic-migration.md` |
| `DailyAnalysisLog.strategy_type` 欄位已存在 | 現有欄位，確認有值即可 |
| 歷史 `DailyAnalysisLog` 有 `strategy_type` 記錄 | 初期樣本可能為 0，不影響腳本正確性 |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| `strategy_version` 遞增的觸發條件是否需要文件化？ | 決定：記錄在 `config.py` docstring，只在 `strategy_generator.py` / `confidence_scorer.py` 有實質邏輯變更時手動遞增 |
| 舊 log 格式（`[Router] 命中 ...`）是否完全取代？ | 決定：是，同步替換為結構化格式，語意不變 |
