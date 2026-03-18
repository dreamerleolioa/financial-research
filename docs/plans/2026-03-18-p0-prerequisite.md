# P0 前置：回測地基建設 Implementation Plan

> 類型：Implementation Plan
> 建立日期：2026-03-18
> 對應 Roadmap：`docs/research/post-new-position-strategy-optimization-roadmap.md` §5.0 / §5.0b / §5.0c

---

## 1. 背景

Roadmap 定義了三項 P0 前置工作，必須在啟動回測校準（P0 主體）之前完成：

1. **回測腳本基礎建設**：指標定義統一、輸出格式標準化
2. **策略版本號佔位**：讓回測結果從一開始就可追溯
3. **資料源品質監控（基礎版）**：確認回測樣本的資料品質

目前現況：

- `backtest_win_rate.py` 已存在且可執行，但「新倉策略」回測口徑（`/analyze` 路由）尚未建立，現有腳本只針對 `Exit/Trim` 訊號（持股診斷的 `action_tag`）
- `strategy_version` 欄位完全不存在（DB、cache、log 均無）
- 資料源有三層 fallback 路由，但無成功率監控、無聚合統計

---

## 2. 目標

1. 新增**新倉策略回測**的指標定義與腳本擴充，與現有 Exit/Trim 回測共用基礎設施
2. 在 DB 模型與輸出欄位加入**最簡 `strategy_version`**，不做完整版本化系統
3. 在資料源 router 層加入**基礎成功率 logging**，可從 log 推算 fallback 命中率

---

## 3. 範圍與非目標

**範圍內：**

- `DailyAnalysisLog` / `StockAnalysisCache` 加入 `strategy_version` 欄位
- `backtest_win_rate.py` 新增新倉策略回測模式（`--mode new-position`）
- 統一新倉策略的指標定義（持有週期、勝率計算邏輯）
- 資料源 router 加入結構化 log（provider name、是否成功、fallback 次數）

**範圍外（留給後續）：**

- 完整策略版本化系統（P2）
- Provider health dashboard / 告警機制（P1）
- 信心分數校準（P0 主體）
- 回測報表 UI（P1+）

---

## 4. 受影響模組

| 模組 | 變更類型 |
|---|---|
| `db/models.py` | 新增 `strategy_version` 欄位 |
| `alembic/` | 新增 migration |
| `api.py` / `graph/nodes.py` | 寫入 `strategy_version` |
| `config.py` | 新增 `STRATEGY_VERSION` 常數 |
| `scripts/backtest_win_rate.py` | 新增 `--mode new-position` |
| `data_sources/institutional_flow/router.py` | 加強結構化 logging |
| `data_sources/yfinance_client.py` | 加強結構化 logging |
| `data_sources/rss_news_client.py` | 加強結構化 logging |

---

## 5. 實作步驟

### Task 1：新增 `strategy_version` 常數與 DB 欄位

**5.1.1 `config.py` 新增版本常數**

```python
# backend/src/ai_stock_sentinel/config.py
STRATEGY_VERSION = "1.0.0"
```

規範：語意版本，只在策略邏輯（`strategy_generator.py`、`confidence_scorer.py`）有實質變更時手動遞增。完整自動化版本管理留給 P2。

**5.1.2 `db/models.py` 新增欄位**

在 `DailyAnalysisLog` 與 `StockAnalysisCache` 各加入：

```python
strategy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

放在 `signal_confidence` 欄位之後。

**5.1.3 Alembic migration**

```bash
cd backend
alembic revision --autogenerate -m "add strategy_version to analysis tables"
alembic upgrade head
```

**5.1.4 寫入邏輯**

在 `graph/nodes.py` 的 `save_to_db` 或對應寫入節點，補入：

```python
from ai_stock_sentinel.config import STRATEGY_VERSION
# ...
log.strategy_version = STRATEGY_VERSION
```

**驗收：**
- `DailyAnalysisLog` 新記錄的 `strategy_version` 為 `"1.0.0"`
- 舊記錄的 `strategy_version` 為 `NULL`（可接受，歷史資料無需回填）

---

### Task 2：新倉策略回測指標定義與腳本擴充

**背景說明：**

現有腳本針對持股診斷訊號（`action_tag = Exit/Trim`），判斷訊號後 5 日股價是否下跌 > 3%。新倉策略的方向相反：判斷「新倉建議後，N 日後股價是否上漲超過門檻」。

**5.2.1 指標定義（統一納入文件）**

| 指標 | 定義 |
|---|---|
| 樣本來源 | `DailyAnalysisLog.strategy_type IN ('short_term', 'mid_term')`（排除 `defensive_wait`） |
| 持有週期 | 5 / 10 / 20 交易日（三組獨立計算） |
| 勝率定義 | 訊號發出後第 N 個交易日收盤價，相對訊號日收盤價漲幅 > +3% |
| 報酬計算 | 絕對報酬（`(pN - p0) / p0 * 100`），不做大盤相對化（留給 P0 主體深化） |
| 平手歸類 | 漲幅介於 -3% ~ +3% 視為平手，不計入勝或敗 |
| 樣本限制 | `analysis_is_final = TRUE`（同現有腳本預設） |

**5.2.2 `backtest_win_rate.py` 擴充**

新增 `--mode` 參數：

- `--mode position`：現有 Exit/Trim 回測（預設，向後相容）
- `--mode new-position`：新倉策略回測
- `--hold-days`：持有天數，`new-position` 模式下可指定 5 / 10 / 20，預設 5

新倉模式的邏輯差異：

1. `fetch_logs` 改為篩選 `strategy_type IN ('short_term', 'mid_term')`，而非 `action_tag`
2. `compute_win_rate` 的閾值改為 `+3.0`（上漲），而非 `-3.0`（下跌）
3. 分桶統計改為按 `conviction_level`（`low` / `medium` / `high`）與 `strategy_type` 分組
4. 持有天數從 `--hold-days` 讀取，`_get_nth_trading_close` 的 `n` 參數對應調整

**驗收：**
- `python scripts/backtest_win_rate.py --mode new-position --days 90` 可執行並輸出結果
- `python scripts/backtest_win_rate.py --mode new-position --hold-days 10` 可執行
- 現有 `--mode position`（或無 `--mode`）行為不變

---

### Task 3：資料源基礎成功率 logging

**目標：** 讓每次 provider 嘗試都輸出結構化 log，事後可從 log 推算成功率與 fallback 命中率，不需要額外資料庫或 dashboard（留給 P1）。

**5.3.1 統一 log 格式**

在各資料源的成功/失敗路徑，改用結構化 JSON log：

```python
import json, logging
logger = logging.getLogger(__name__)

# 成功時
logger.info(json.dumps({
    "event": "provider_success",
    "provider": provider.name,
    "symbol": symbol,
    "is_fallback": attempted_index > 0,  # 非第一順位即為 fallback
}))

# 失敗時
logger.warning(json.dumps({
    "event": "provider_failure",
    "provider": provider.name,
    "symbol": symbol,
    "error_code": exc.code if hasattr(exc, "code") else "unknown",
}))
```

**受影響位置：**

1. `institutional_flow/router.py`：`fetch_daily_flow` 的命中/失敗路徑
2. `yfinance_client.py`：價格與技術指標抓取的成功/失敗路徑
3. `rss_news_client.py`：RSS 新聞抓取的成功/失敗路徑

**5.3.2 不做的事**

- 不建立 DB 表格來持久化 provider 健康狀態（P1 才做）
- 不建立 dashboard（P1 才做）
- 不設告警（P1 才做）

**驗收：**
- 執行一次 `/analyze` 後，log 輸出可見 `provider_success` 或 `provider_failure` 事件
- 可用 `grep 'provider_success\|provider_failure'` 從 log 統計成功率
- 舊的 `logger.info("[Router] 命中 ...")`、`logger.warning("[Router] ... 失敗")` 改為結構化格式（不改語意）

---

## 6. 測試計劃

| 測試 | 方法 |
|---|---|
| `strategy_version` 寫入 | 執行一次 `/analyze`，查 DB 確認欄位有值 |
| migration 可逆 | `alembic downgrade -1` 不報錯 |
| 新倉回測腳本 | `python scripts/backtest_win_rate.py --mode new-position --days 30` 輸出報告（樣本數可為 0，但不應報錯） |
| 舊回測腳本向後相容 | `python scripts/backtest_win_rate.py --days 30` 輸出與修改前一致 |
| provider log | 執行 `/analyze` 並確認 log 有 `provider_success` 事件 |

---

## 7. 依賴與風險

| 項目 | 說明 |
|---|---|
| Alembic 環境 | 需確認本機與 Render 的 migration 流程正常（參考 `2026-03-11-alembic-migration.md`） |
| 新倉回測樣本數 | 若歷史 `DailyAnalysisLog` 中 `strategy_type` 欄位為 NULL（舊資料），初期樣本數可能為 0，不影響腳本正確性 |
| log 格式變更 | 結構化 log 改變了 router 的 log 字串格式，若有外部工具依賴舊格式需注意 |

---

## 8. 驗收定義

全部完成的標準：

1. `DailyAnalysisLog.strategy_version` 欄位存在，新分析記錄有值
2. `python scripts/backtest_win_rate.py --mode new-position` 可正常執行
3. `python scripts/backtest_win_rate.py`（無 `--mode`）行為不變
4. 執行 `/analyze` 後 log 有 `provider_success` / `provider_failure` 結構化事件
5. 以上均有對應測試或手動驗證記錄

---

## 9. Spec Review

對應需求規格：`docs/p0-prerequisite-spec.md`

實作前請確認 spec 中以下項目無歧義：

- F1-1 ~ F1-6：`strategy_version` 欄位行為與 migration 可逆性
- F2（指標定義表）：勝率門檻、平手定義、樣本限制是否與現有腳本邏輯一致
- F3-1 ~ F3-6：`--mode` 參數行為與向後相容條件
- F4-5 ~ F4-6：provider log 的 JSON 欄位命名是否與現有 logging 結構相容

驗收時對照 spec AC1 ~ AC6 逐條確認。
