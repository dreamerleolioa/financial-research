# P2：策略版本化（完整版）需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-19-p2-p3-implementations.md`
> 對應 Roadmap：§5.7
> 前置依賴：P0 前置完成（`strategy_version` 欄位佔位已存在）

---

## 1. 背景問題

P0 前置已建立 `STRATEGY_VERSION = "1.0.0"` 常數與 DB 欄位作為佔位，但版本化系統仍不完整：

1. **版本遞增沒有規範觸發條件**：何時應該 bump `1.0.0 → 1.1.0` 或 `1.0.0 → 2.0.0` 目前只有 docstring 說明，無正式 spec
2. **`analysis_detail` 不帶版本資訊**：API response 中看不出這次分析使用了哪個版本的策略邏輯
3. **回測結果雖已記錄 `strategy_version`（P1 完成），但尚未支援跨版本比較**：無法查詢「v1.0.0 的勝率 vs v1.1.0 的勝率」
4. **快取未作版本失效**：若 `STRATEGY_VERSION` 升版，舊版快取的結果可能仍在 `StockAnalysisCache` 中被回傳，混淆版本
5. **log / backtest 的版本欄位是 nullable**：舊記錄為 NULL，版本切分查詢時需要特別處理

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 正式定義語意化版本號的遞增規則（major / minor / patch） |
| G2 | API response 的 `analysis_detail` 加入 `strategy_version` 欄位 |
| G3 | 快取寫入與讀取加入版本一致性檢查，升版後舊快取自動失效 |
| G4 | `backtest_win_rate.py` 支援 `--strategy-version` 過濾，可做跨版本比較 |
| G5 | `docs/development-execution-playbook.md` 補充版本遞增的操作 SOP |

---

## 3. 範圍

### 範圍內

- `config.py`：`STRATEGY_VERSION` 版本號更新規則文件化（docstring 精確化）
- `api.py`：`AnalyzeResponse` 加入 `strategy_version` 欄位
- `api.py`：快取讀取時加入版本一致性檢查（若快取版本 ≠ 當前版本，視為失效）
- `scripts/backtest_win_rate.py`：新增 `--strategy-version` 過濾參數
- `docs/development-execution-playbook.md`：版本遞增 SOP 章節

### 範圍外

- 自動化版本遞增（hook 或 CI）
- 前端顯示 `strategy_version`（使用者不需要看到）
- 跨版本 A/B 測試框架
- `DailyAnalysisLog` 的歷史 NULL 版本回填

---

## 4. 版本號語意定義（正式 Spec）

採用語意化版本 `MAJOR.MINOR.PATCH`：

| 版次 | 觸發條件 | 範例 |
|---|---|---|
| PATCH | 只修改 docstring、log 格式、非邏輯性重構 | `1.0.0 → 1.0.1` |
| MINOR | 修改 `confidence_scorer.py` 的常數值（調權）；修改 `generate_action_plan()` 的文字模板；修改 `_determine_conviction_level()` 的降級閾值 | `1.0.0 → 1.1.0` |
| MAJOR | 修改 `generate_strategy()` 的核心 evidence scoring 邏輯；修改策略分類規則（新增或移除 `strategy_type`）；修改 `confidence_scorer.py` 的計算架構（非常數） | `1.0.0 → 2.0.0` |

**何時不需要 bump：**
- 修改 `langchain_analyzer.py` 的 LLM prompt（屬 prompt 版本，以 `prompt_hash` 追蹤）
- 修改前端 UI
- 修改 DB schema（非策略邏輯）

---

## 5. 功能需求

### F1：API response 加入 strategy_version

| 編號 | 需求 |
|---|---|
| F1-1 | `AnalyzeResponse` model 新增 `strategy_version: str \| None` 欄位 |
| F1-2 | `/analyze` 端點回傳 `strategy_version = config.STRATEGY_VERSION` |
| F1-3 | 從快取命中回傳時，`strategy_version` 讀自快取記錄的 `strategy_version` 欄位 |
| F1-4 | 舊快取記錄 `strategy_version` 為 NULL 時，回傳 `null`（不填入當前版本） |

### F2：快取版本一致性檢查

| 編號 | 需求 |
|---|---|
| F2-1 | 讀取 `StockAnalysisCache` 快取時，若 `cache.strategy_version != STRATEGY_VERSION`，視為版本失效，不回傳快取 |
| F2-2 | 版本失效時，重新執行完整分析流程（等同快取 miss） |
| F2-3 | `cache.strategy_version = NULL` 時（舊資料），同樣視為版本失效 |
| F2-4 | 版本失效的快取記錄不刪除（讓 DB 保留歷史），只是本次請求觸發重新分析 |

### F3：回測跨版本過濾

| 編號 | 需求 |
|---|---|
| F3-1 | `backtest_win_rate.py` 新增 `--strategy-version` 參數（可接受多個值，以逗號分隔） |
| F3-2 | 指定版本時，只回測 `DailyAnalysisLog.strategy_version` 符合的記錄 |
| F3-3 | 不指定時，行為與現在一致（不過濾版本） |
| F3-4 | `--strategy-version NULL` 表示只回測舊記錄（`strategy_version IS NULL`） |
| F3-5 | 輸出 console 摘要時顯示此次回測使用的版本過濾條件 |

### F4：版本遞增 SOP 文件化

| 編號 | 需求 |
|---|---|
| F4-1 | `docs/development-execution-playbook.md` 新增「策略版本遞增 SOP」章節 |
| F4-2 | SOP 包含：觸發條件判斷表（對應上方 §4 定義）、操作步驟（修改 config.py → commit → 重跑回測） |
| F4-3 | SOP 明確說明：版本遞增後，現有 `StockAnalysisCache` 會自動觸發重新分析（因 F2 機制） |

---

## 6. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | `AnalyzeResponse` 加入 `strategy_version` 欄位為向後相容（nullable），不破壞現有 API consumer |
| NF2 | 快取版本檢查在現有 `_handle_cache_hit()` 函數內完成，不增加額外 DB 查詢 |
| NF3 | 前端不需要處理 `strategy_version` 欄位（接收即可，不渲染） |

---

## 7. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | `/analyze` response 含 `strategy_version: "1.0.0"` | API 呼叫確認 |
| AC2 | 修改 `STRATEGY_VERSION` 為 `"1.1.0"` 後，舊快取不再被回傳（觸發重分析） | 本機修改測試 |
| AC3 | 修改後還原 `"1.0.0"`，快取恢復正常命中 | 本機測試 |
| AC4 | `--strategy-version 1.0.0` 只輸出對應版本的回測記錄 | CLI 執行 |
| AC5 | `development-execution-playbook.md` 有「策略版本遞增 SOP」章節 | 確認文件 |

---

## 8. 依賴

| 依賴項目 | 說明 |
|---|---|
| P0 前置完成 | `strategy_version` 欄位已存在於 `DailyAnalysisLog` 和 `StockAnalysisCache` |
| P1 回測持久化完成 | `BacktestRun.strategy_version` 欄位已存在 |

---

## 9. 開放問題

| 問題 | 狀態 |
|---|---|
| 快取版本失效是否應有 log 記錄？ | 決定：是，`logger.info` 輸出版本失效事件，欄位含 `symbol`、`cache_version`、`current_version` |
| `--strategy-version` 是否支援萬用字元（如 `1.*`）？ | 決定：不支援，P2 只做精確匹配，萬用字元留後續 |
