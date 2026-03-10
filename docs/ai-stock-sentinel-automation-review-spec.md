# AI Stock Sentinel 自動化復盤與數據循環系統技術規格

> 類型：Phase 7 系統擴展文件
> 日期：2026-03-10
> 狀態：Draft v1.0
> 定位：將單次診斷系統升級為具備記憶、自我修正能力的閉環量化平台
> 前置依賴：Phase 6 持股診斷系統（`POST /analyze/position`）完成

---

## 1. 系統定位與目標

### 1.1 從「工具」到「閉環平台」

Phase 1–6 完成了從市場偵察到持股診斷的核心分析能力，但每次呼叫 API 仍屬**一次性、無記憶**的分析。Phase 7 的核心命題是：

> **讓系統記住昨天說了什麼，並在今天說出更聰明的話。**

透過引入 **n8n（自動化調度中樞）** 與 **Self-hosted PostgreSQL（數據持久化中心）**，系統將從單點分析進化為：

| 能力層級 | Phase 1–6（現況） | Phase 7+（目標） |
|---------|-----------------|----------------|
| 分析範圍 | 單次、單股 | 批次、全倉位 |
| 記憶能力 | 無（每次 Stateless） | 有（歷史 Log 可查詢） |
| 預警機制 | 手動查詢 | 自動偵測 + 即時通知 |
| 模型優化 | 靜態權重 | 基於歷史校準的動態權重 |

### 1.2 架構升級原則

延續 v2.7 的工程紀律，Phase 7 的所有擴展必須遵守：

- **Tool Use 原則不變**：資料庫讀取、指標計算仍由 Python 函式執行，LLM 只負責定性推理
- **禁止 LLM 盲猜歷史數值**：昨日訊號、歷史信心分數必須從 DB 讀取，不得由 LLM 推斷
- **數據主權在本地**：敏感的持倉資料與分析結果儲存於自建 PostgreSQL，不依賴第三方雲端 DB

---

## 2. 數據庫架構（PostgreSQL @ Local Server）

### 2.1 選用 PostgreSQL 的理由

| 需求 | PostgreSQL 優勢 |
|------|----------------|
| 儲存非結構化分析結果（MA/RSI 等指標組合） | **JSONB 型別**：支援高效索引與部分更新，優於純 JSON text |
| 時序型查詢（按日期回溯訊號） | `record_date` + BTREE 索引，搭配 `BETWEEN` 或視窗函式效能穩定 |
| 複雜聚合（勝率統計、信心分布） | 豐富的視窗函式（`LAG`、`LEAD`、`PERCENT_RANK`）原生支援 |
| 長期運維穩定性 | 成熟生態、WAL 備份、pg_dump 方案完備 |

**JSONB vs JSON 型別選擇**：本文件統一使用 `JSONB`，原因是 PostgreSQL 對 JSONB 建立 GIN 索引後，可執行 `@>` 包含查詢，例如直接篩選 `indicators->>'rsi' > '70'`（需搭配 `CAST`），查詢效能遠優於全行掃描。

---

### 2.2 核心 Table Schema

#### Table 1：`user_portfolio`（持倉主表）

```sql
CREATE TABLE user_portfolio (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20)     NOT NULL,           -- 股票代碼，例：2330.TW
    entry_price     NUMERIC(10, 2)  NOT NULL,           -- 購入成本價（診斷核心錨點）
    quantity        INTEGER         NOT NULL DEFAULT 0, -- 持有股數
    entry_date      DATE            NOT NULL,           -- 購入日期
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE, -- FALSE = 已出場，保留歷史紀錄
    notes           TEXT,                               -- 自由備註（購入理由、策略標記）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 唯一約束：同一 symbol 不允許重複的 active 倉位
CREATE UNIQUE INDEX uq_portfolio_active_symbol
    ON user_portfolio (symbol)
    WHERE is_active = TRUE;

-- 查詢用索引
CREATE INDEX idx_portfolio_symbol ON user_portfolio (symbol);
CREATE INDEX idx_portfolio_active ON user_portfolio (is_active);
```

**欄位說明**

| 欄位 | 說明 |
|------|------|
| `is_active` | `TRUE` = 仍持有。出場後設為 `FALSE` 而非刪除，確保歷史診斷 log 可回溯 |
| `entry_price` | 與 `POST /analyze/position` 的 `entry_price` 直接對應，自動化流程從此欄讀取 |
| `notes` | 非必填，可記錄「買進理由」，未來可作為反思提示的上下文 |

---

#### Table 2：`daily_analysis_log`（每日診斷紀錄表）

```sql
CREATE TABLE daily_analysis_log (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20)     NOT NULL,
    record_date         DATE            NOT NULL,           -- 分析執行的交易日
    signal_confidence   NUMERIC(5, 2),                     -- 信心分數，例：72.50
    action_tag          VARCHAR(20),                        -- Hold / Trim / Exit / Add
    recommended_action  TEXT,                               -- 完整建議描述（中文）
    indicators          JSONB,                              -- 技術指標快照（見下方範例）
    final_verdict       TEXT,                               -- LLM 產出的完整診斷結論
    prev_action_tag     VARCHAR(20),                        -- 昨日 action_tag（用於訊號轉向偵測）
    prev_confidence     NUMERIC(5, 2),                      -- 昨日信心分數（對比用）
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 查詢用索引
CREATE UNIQUE INDEX uq_log_symbol_date
    ON daily_analysis_log (symbol, record_date);  -- 確保每股每日只有一筆

CREATE INDEX idx_log_symbol ON daily_analysis_log (symbol);
CREATE INDEX idx_log_record_date ON daily_analysis_log (record_date);
CREATE INDEX idx_log_action_tag ON daily_analysis_log (action_tag);

-- JSONB GIN 索引：支援指標值的高效查詢
CREATE INDEX idx_log_indicators_gin
    ON daily_analysis_log USING GIN (indicators);
```

**`indicators` JSONB 範例結構**

```json
{
  "ma5":          975.0,
  "ma20":         960.0,
  "ma60":         940.0,
  "rsi_14":       68.5,
  "bias_20":      1.56,
  "volume_ratio": 1.23,
  "close_price":  985.0,
  "institutional": {
    "foreign_net":  12500,
    "trust_net":     3200,
    "dealer_net":    -800
  }
}
```

**JSONB 查詢範例**

```sql
-- 查詢昨日 RSI 超過 70 的所有持股（過熱篩選）
SELECT symbol, record_date, (indicators->>'rsi_14')::NUMERIC AS rsi
FROM daily_analysis_log
WHERE record_date = CURRENT_DATE - 1
  AND (indicators->>'rsi_14')::NUMERIC > 70;

-- 查詢包含外資買超資訊的紀錄
SELECT symbol, indicators->'institutional'->>'foreign_net' AS foreign_net
FROM daily_analysis_log
WHERE indicators @> '{"institutional": {}}'
  AND record_date >= CURRENT_DATE - 7;
```

---

## 3. n8n 自動化工作流設計

### 3.1 架構總覽

```
n8n (Zeabur/雲端)
    │
    ├─── 每日診斷流 (Cron: 每日 18:30)
    │       └─── 讀持倉 → 批次呼叫 API → 寫回 DB
    │
    ├─── 風險預警流 (Webhook: 每日診斷流完成後觸發)
    │       └─── 判斷 action_tag → Telegram/Line 通知
    │
    └─── 優化回測流 (Cron: 每週日 08:00)
            └─── 統計信心分數 vs 實際漲跌 → 產出報告

PostgreSQL (Local Server)
    ├── user_portfolio
    └── daily_analysis_log

FastAPI Backend (AI Stock Sentinel)
    └── POST /analyze/position
```

**安全性注意事項**：n8n 若架設於雲端（如 Zeabur），與本地 PostgreSQL 的連線必須經過安全隧道：

- **推薦方案 A：Cloudflare Tunnel**（零開放埠，最安全）
  - 本地伺服器安裝 `cloudflared`，建立 Named Tunnel，指向 PostgreSQL port 5432
  - n8n 的 Postgres Node 連接 Cloudflare 提供的私有 hostname
- **備選方案 B：SSH Tunnel**
  - n8n 伺服器以 SSH Port Forwarding 連接本地 PostgreSQL
  - 搭配 IP 白名單限制 SSH 來源
- **禁止做法**：直接將 5432 port 開放至公網，即使有密碼保護仍屬高風險

---

### 3.2 工作流 A：每日診斷流

**觸發條件**：Cron 表達式 `30 18 * * 1-5`（台灣時間，週一至週五收盤後）

**完整節點設計**

```
[Cron Trigger]
    │
    ▼
[Postgres Node] ── 查詢 user_portfolio WHERE is_active = TRUE
    │ 回傳: [{symbol, entry_price, quantity, entry_date}, ...]
    │
    ▼
[Split In Batches] ── 每批 1 筆（避免 API 過載，可調整）
    │
    ▼
[HTTP Request Node] ── POST /analyze/position
    │ Body: {symbol, entry_price, entry_date, quantity}
    │ Headers: {Authorization: Bearer <API_KEY>}
    │
    ▼
[Function Node] ── 解析 API Response，提取關鍵欄位
    │ 輸出: {symbol, signal_confidence, action_tag,
    │        recommended_action, indicators, final_verdict}
    │
    ▼
[Postgres Node] ── UPSERT into daily_analysis_log
    │ ON CONFLICT (symbol, record_date) DO UPDATE SET ...
    │
    ▼
[Webhook Trigger] ── 觸發「風險預警流」
```

**UPSERT SQL（n8n Postgres Node 使用）**

```sql
INSERT INTO daily_analysis_log (
    symbol, record_date, signal_confidence, action_tag,
    recommended_action, indicators, final_verdict,
    prev_action_tag, prev_confidence
)
VALUES (
    $1, CURRENT_DATE, $2, $3, $4, $5::JSONB, $6,
    -- 從昨日 log 帶入對比數據
    (SELECT action_tag    FROM daily_analysis_log WHERE symbol = $1 AND record_date = CURRENT_DATE - 1),
    (SELECT signal_confidence FROM daily_analysis_log WHERE symbol = $1 AND record_date = CURRENT_DATE - 1)
)
ON CONFLICT (symbol, record_date)
DO UPDATE SET
    signal_confidence  = EXCLUDED.signal_confidence,
    action_tag         = EXCLUDED.action_tag,
    recommended_action = EXCLUDED.recommended_action,
    indicators         = EXCLUDED.indicators,
    final_verdict      = EXCLUDED.final_verdict;
```

---

### 3.3 工作流 B：風險預警流

**觸發條件**：每日診斷流完成後，由 Webhook 觸發

**判斷邏輯**

```
[Webhook Trigger]
    │
    ▼
[Postgres Node] ── 查詢今日所有 action_tag IN ('Exit', 'Trim')
    │
    ▼
[IF Node] ── 是否有需要預警的持股？
    │
    ├─ YES ──▶ [Function Node] 組裝通知訊息
    │               │
    │               ▼
    │          [Telegram Node / Line Notify Node]
    │               發送格式化預警訊息
    │
    └─ NO ───▶ [NoOp] 結束，不發送通知
```

**Telegram 訊息格式範例**

```
🚨 AI Stock Sentinel 風險預警 (2026-03-10)

以下持股建議注意：

📌 2330.TW（台積電）
   建議：Exit（出場）
   信心分數：78.5
   摘要：RSI 已達 76，外資連三日賣超，建議逢高減碼...

📌 2454.TW（聯發科）
   建議：Trim（減碼 30%）
   信心分數：65.0
   摘要：均線空頭排列，月線下彎...

⚠️ 以上建議僅供參考，請結合個人判斷操作。
```

**訊號轉向偵測**：若 `prev_action_tag` 與今日 `action_tag` 不同，在通知中加入轉向標記：

```
🔄 訊號轉向：Hold → Exit（較昨日信心分數下降 12.5 點）
```

---

### 3.4 工作流 C：優化回測流

**觸發條件**：Cron `0 8 * * 0`（每週日早上 08:00）

**目的**：統計過去 N 週內，各 `action_tag` 建議後的實際股價走勢，校驗信心分數的預測準確性。

**核心查詢**

```sql
-- 統計各 action_tag 的信心分數分布
SELECT
    action_tag,
    COUNT(*)                              AS total_signals,
    AVG(signal_confidence)                AS avg_confidence,
    PERCENTILE_CONT(0.5) WITHIN GROUP
        (ORDER BY signal_confidence)      AS median_confidence,
    MIN(signal_confidence)                AS min_confidence,
    MAX(signal_confidence)                AS max_confidence
FROM daily_analysis_log
WHERE record_date >= CURRENT_DATE - 30
GROUP BY action_tag
ORDER BY avg_confidence DESC;
```

```sql
-- 信心分數時序趨勢（單股，近 20 個交易日）
SELECT
    record_date,
    signal_confidence,
    action_tag,
    LAG(signal_confidence) OVER (PARTITION BY symbol ORDER BY record_date) AS prev_confidence,
    signal_confidence - LAG(signal_confidence) OVER (PARTITION BY symbol ORDER BY record_date) AS confidence_delta
FROM daily_analysis_log
WHERE symbol = '2330.TW'
  AND record_date >= CURRENT_DATE - 30
ORDER BY record_date;
```

**產出報告格式**（寫入 n8n 的 Google Sheets Node 或本地 CSV）

```
週報期間：2026-03-03 ~ 2026-03-09

【信心分數校準報告】
action_tag | 訊號次數 | 平均信心 | 中位信心
Hold       |   35    |   61.2   |   62.0
Trim       |    8    |   72.5   |   71.8
Exit       |    4    |   79.1   |   80.0
Add        |    3    |   68.3   |   67.5

【建議】
- Exit 訊號的平均信心（79.1）顯著高於 Hold（61.2），閾值設定合理
- Trim 與 Exit 訊號分界建議維持在 75.0
```

---

## 4. 邏輯升級與模型優化 Roadmap

### 4.1 歷史對比敘事（API 層升級）

**目標**：`POST /analyze/position` 的 `final_verdict` 能包含「訊號轉向分析」，而非每次都是孤立的當日診斷。

**實作方式**：

1. API 接收請求後，在呼叫 LangGraph 前，先執行 DB 查詢取得昨日數據：

```python
# backend/services/history_loader.py

async def load_yesterday_context(symbol: str, db: AsyncSession) -> dict | None:
    """從 DB 讀取昨日分析結果，作為 LLM 的歷史上下文。"""
    result = await db.execute(
        select(DailyAnalysisLog)
        .where(
            DailyAnalysisLog.symbol == symbol,
            DailyAnalysisLog.record_date == date.today() - timedelta(days=1)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {
        "prev_action_tag":   row.action_tag,
        "prev_confidence":   float(row.signal_confidence),
        "prev_rsi":          row.indicators.get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(row.indicators),
    }
```

2. 將歷史上下文注入 LLM Prompt，在 `final_verdict` 段落加入轉向說明：

```
【訊號連續性分析】
昨日建議：Hold（信心：61.5）
今日建議：Trim（信心：74.0）

訊號轉向原因：
- RSI 從昨日 65.2 上升至今日 73.8，進入超買區
- 外資今日轉為賣超 8,500 張，較昨日買超 2,100 張大幅反轉
- 均線排列未變（多頭），但短期超買壓力加大，建議先行減碼 30%
```

**Tool Use 原則堅守**：`prev_rsi`、`prev_confidence` 等數值**必須從 DB 讀取**後傳入 Prompt，嚴禁 LLM 自行猜測昨日數值。

---

### 4.2 策略勝率校準（`confidence_scorer.py` 動態調權）

**目標**：根據累積數據，調整各維度在信心分數計算中的加權比例。

**現有架構**（靜態權重，位於 `confidence_scorer.py`）：

```python
DIMENSION_WEIGHTS = {
    "technical":     0.35,
    "institutional": 0.30,
    "news":          0.20,
    "fundamental":   0.15,
}
```

**Phase 8+ 校準流程**：

1. **數據收集期**（Phase 7，前 3 個月）：持續累積 `daily_analysis_log`，記錄各次診斷的維度分數。

2. **勝率回測**（Phase 8）：
   - 定義「勝率」：Exit 訊號後 5 個交易日內，股價確實下跌 > 3% 視為正確預測
   - 需額外引入真實股價數據（yfinance 查詢回測期間的收盤價）

3. **校準分析**：

```python
# 分析各維度分數與預測結果的相關性
# 若技術面分數高但預測勝率低，考慮降低技術面權重
correlation_matrix = {
    "technical_score vs outcome":     pearsonr(tech_scores, outcomes),
    "institutional_score vs outcome": pearsonr(inst_scores, outcomes),
    "news_score vs outcome":          pearsonr(news_scores, outcomes),
}
```

4. **人工審核後調整**：校準結果提交人工確認，不自動寫入生產環境。

---

## 5. 實作任務與優先序

### Phase 7：基礎設施建置（本文件範圍）

| 優先序 | 任務 | 說明 | 預估工時 |
|--------|------|------|---------|
| P0 | PostgreSQL 本地部署 | Docker Compose 部署，設定 WAL 備份 | 0.5 天 |
| P0 | 執行 Table Schema 建立 | 執行本文件 Section 2 的 DDL | 0.5 天 |
| P1 | SQLAlchemy 接入（FastAPI） | 建立 `DailyAnalysisLog` ORM Model，實作 CRUD | 1 天 |
| P1 | `history_loader.py` 實作 | 昨日上下文讀取服務（Section 4.1） | 0.5 天 |
| P1 | n8n 每日診斷流部署 | 建立 Workflow A（Section 3.2） | 1 天 |
| P2 | 安全隧道設定（Cloudflare Tunnel） | 保護 n8n 至本地 DB 的連線 | 0.5 天 |
| P2 | 風險預警流部署 | 建立 Workflow B + Telegram Bot | 0.5 天 |
| P3 | 優化回測流部署 | 建立 Workflow C + 週報產出 | 1 天 |

### Phase 8：邏輯強化（後續規劃）

| 任務 | 說明 |
|------|------|
| 訊號轉向敘事 | API 整合 `history_loader`，Prompt 升級 |
| 勝率回測腳本 | 引入 yfinance 歷史價格，比對診斷準確率 |
| 信心分數校準 | 基於回測結果的半自動調權流程 |

### Phase 9：平台化（長期願景）

| 任務 | 說明 |
|------|------|
| 多使用者倉位管理 | `user_id` 接入，支援多帳號 |
| 前端復盤儀表板 | 視覺化信心分數時序、訊號轉向歷史 |
| 自動化回測報告 | PDF 產出，包含勝率統計與建議調整方向 |

---

## 6. 關鍵設計決策記錄

| 決策 | 選擇 | 理由 |
|------|------|------|
| DB 型別 | PostgreSQL（JSONB） | JSONB GIN 索引支援指標值的高效查詢 |
| 自動化引擎 | n8n | 低代碼、支援 Postgres Node、Webhook、排程，適合小型量化平台 |
| 連線安全 | Cloudflare Tunnel | 零開放埠，最安全的本地 DB 暴露方案 |
| 歷史數據注入 | DB 查詢後傳入 Prompt | 嚴守 Tool Use 原則，LLM 不猜歷史數值 |
| 權重校準流程 | 人工審核後調整 | 防止自動調權引入系統性偏差 |
| 出場後的倉位處理 | `is_active = FALSE`（軟刪除） | 保留歷史診斷 log 的可追溯性 |

---

*文件版本：v1.0 | 最後更新：2026-03-10 | 下一步：執行 Phase 7 P0 任務（PostgreSQL 部署）*
