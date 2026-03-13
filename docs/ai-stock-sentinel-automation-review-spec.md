# AI Stock Sentinel 自動化復盤與數據循環系統技術規格

> 類型：Phase 7 系統擴展文件
> 日期：2026-03-12
> 狀態：Draft v1.6
> 定位：將單次診斷系統升級為具備記憶、自我修正能力的閉環量化平台
> 前置依賴：Phase 6 持股診斷系統（`POST /analyze/position`）完成

---

## 1. 系統定位與目標

### 1.1 從「工具」到「閉環平台」

Phase 1–6 完成了從市場偵察到持股診斷的核心分析能力，但每次呼叫 API 仍屬**一次性、無記憶**的分析。Phase 7 的核心命題是：

> **讓系統記住昨天說了什麼，並在今天說出更聰明的話。**

透過引入 **n8n（自動化調度中樞）** 與 **Self-hosted PostgreSQL（數據持久化中心）**，系統將從單點分析進化為：

| 能力層級 | Phase 1–6（現況）    | Phase 7+（目標）       |
| -------- | -------------------- | ---------------------- |
| 分析範圍 | 單次、單股           | 批次、全倉位           |
| 記憶能力 | 無（每次 Stateless） | 有（歷史 Log 可查詢）  |
| 預警機制 | 手動查詢             | 自動偵測 + 即時通知    |
| 模型優化 | 靜態權重             | 基於歷史校準的動態權重 |

### 1.2 架構升級原則

延續 v2.7 的工程紀律，Phase 7 的所有擴展必須遵守：

- **Tool Use 原則不變**：資料庫讀取、指標計算仍由 Python 函式執行，LLM 只負責定性推理
- **禁止 LLM 盲猜歷史數值**：昨日訊號、歷史信心分數必須從 DB 讀取，不得由 LLM 推斷
- **數據主權在本地**：敏感的持倉資料與分析結果儲存於自建 PostgreSQL，不依賴第三方雲端 DB

---

## 2. 數據庫架構（PostgreSQL @ Local Server）

### 2.1 選用 PostgreSQL 的理由

| 需求                                      | PostgreSQL 優勢                                               |
| ----------------------------------------- | ------------------------------------------------------------- |
| 儲存非結構化分析結果（MA/RSI 等指標組合） | **JSONB 型別**：支援高效索引與部分更新，優於純 JSON text      |
| 時序型查詢（按日期回溯訊號）              | `record_date` + BTREE 索引，搭配 `BETWEEN` 或視窗函式效能穩定 |
| 複雜聚合（勝率統計、信心分布）            | 豐富的視窗函式（`LAG`、`LEAD`、`PERCENT_RANK`）原生支援       |
| 長期運維穩定性                            | 成熟生態、WAL 備份、pg_dump 方案完備                          |

**JSONB vs JSON 型別選擇**：本文件統一使用 `JSONB`，原因是 PostgreSQL 對 JSONB 建立 GIN 索引後，可執行 `@>` 包含查詢，例如直接篩選 `indicators->>'rsi' > '70'`（需搭配 `CAST`），查詢效能遠優於全行掃描。

---

### 2.2 核心 Table Schema

#### Table 0：`users`（使用者主表）

```sql
CREATE TABLE users (
    id          SERIAL PRIMARY KEY,
    google_sub  VARCHAR(255) NOT NULL UNIQUE,  -- Google id_token 的 sub 欄位，比 email 穩定
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255),
    avatar_url  TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    deleted_at  TIMESTAMPTZ,                   -- 軟刪除，帳號刪除後歷史 log 仍可保留
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_google_sub ON users (google_sub);
CREATE INDEX idx_users_email ON users (email);
```

**欄位說明**

| 欄位         | 說明                                                            |
| ------------ | --------------------------------------------------------------- |
| `google_sub` | Google 的不可變使用者識別碼，即使使用者更改 email 也不會變      |
| `deleted_at` | 軟刪除時間戳，設值後視為已刪除帳號，歷史 log 以去識別化方式保留 |
| `is_active`  | 預留管理員停用帳號的能力                                        |

---

#### Table 1：`user_portfolio`（持倉主表）

**持倉上限：每位使用者最多 5 筆 `is_active = TRUE` 的持倉。** 新增持倉時，後端須先計算該使用者目前 active 持倉數，若已達 5 筆則回傳 `HTTP 422`。

```sql
CREATE TABLE user_portfolio (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER         REFERENCES users(id) ON DELETE SET NULL, -- 可空，使用者刪帳號後保留歷史
    symbol          VARCHAR(20)     NOT NULL,           -- 股票代碼，例：2330.TW
    entry_price     NUMERIC(10, 2)  NOT NULL,           -- 購入成本價（診斷核心錨點）
    quantity        INTEGER         NOT NULL DEFAULT 0, -- 持有股數
    entry_date      DATE            NOT NULL,           -- 購入日期
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE, -- FALSE = 已出場，保留歷史紀錄
    notes           TEXT,                               -- 自由備註（購入理由、策略標記）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 唯一約束：同一使用者同一 symbol 不允許重複的 active 倉位
CREATE UNIQUE INDEX uq_portfolio_active_symbol
    ON user_portfolio (user_id, symbol)
    WHERE is_active = TRUE;

-- 查詢用索引
CREATE INDEX idx_portfolio_symbol ON user_portfolio (symbol);
CREATE INDEX idx_portfolio_active ON user_portfolio (is_active);
CREATE INDEX idx_portfolio_user_id ON user_portfolio (user_id);
```

**欄位說明**

| 欄位          | 說明                                                                        |
| ------------- | --------------------------------------------------------------------------- |
| `is_active`   | `TRUE` = 仍持有。出場後設為 `FALSE` 而非刪除，確保歷史診斷 log 可回溯       |
| `entry_price` | 與 `POST /analyze/position` 的 `entry_price` 直接對應，自動化流程從此欄讀取 |
| `notes`       | 非必填，可記錄「買進理由」，未來可作為反思提示的上下文                      |

**持倉上限實作規格**

- 上限：每位使用者最多 **5 筆** active 持倉（`is_active = TRUE`）
- 新增持倉的端點（`POST /portfolio`）在寫入前執行計數查詢：
  ```python
  count = db.query(UserPortfolio).filter_by(user_id=user.id, is_active=True).count()
  if count >= 5:
      raise HTTPException(status_code=422, detail="最多只能追蹤 5 筆持股")
  ```
- 出場（`is_active = False`）不計入上限，上限只針對 active 持倉

---

#### Table 2：`daily_analysis_log`（每日診斷紀錄表）

```sql
CREATE TABLE daily_analysis_log (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER         REFERENCES users(id) ON DELETE SET NULL, -- 可空，去識別化後保留供模型優化
    symbol              VARCHAR(20)     NOT NULL,
    record_date         DATE            NOT NULL,           -- 分析執行的交易日
    signal_confidence   NUMERIC(5, 2),                     -- 信心分數，例：72.50
    action_tag          VARCHAR(20),                        -- Hold / Trim / Exit / Add
    recommended_action  TEXT,                               -- 完整建議描述（中文）
    indicators          JSONB,                              -- 技術指標快照（見下方範例）
    final_verdict       TEXT,                               -- LLM 產出的完整診斷結論
    prev_action_tag     VARCHAR(20),                        -- 昨日 action_tag（用於訊號轉向偵測）
    prev_confidence     NUMERIC(5, 2),                      -- 昨日信心分數（對比用）
    is_final            BOOLEAN         NOT NULL DEFAULT FALSE,  -- FALSE=盤中非定稿；TRUE=收盤定稿（歷史復盤唯一依據）
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 查詢用索引
CREATE UNIQUE INDEX uq_log_user_symbol_date
    ON daily_analysis_log (user_id, symbol, record_date);  -- 確保每位使用者每股每日只有一筆

CREATE INDEX idx_log_symbol ON daily_analysis_log (symbol);
CREATE INDEX idx_log_record_date ON daily_analysis_log (record_date);
CREATE INDEX idx_log_action_tag ON daily_analysis_log (action_tag);
CREATE INDEX idx_log_user_id ON daily_analysis_log (user_id);

-- JSONB GIN 索引：支援結構化包含查詢（@>）
CREATE INDEX idx_log_indicators_gin
    ON daily_analysis_log USING GIN (indicators);

-- 表達式索引：加速範圍比較查詢（GIN 對 > / < 效能受限，需改用表達式索引）
CREATE INDEX idx_log_rsi_value
    ON daily_analysis_log (((indicators->>'rsi_14')::NUMERIC));
CREATE INDEX idx_log_bias_value
    ON daily_analysis_log (((indicators->>'bias_20')::NUMERIC));
```

**`indicators` JSONB 範例結構**

```json
{
  "ma5": 975.0,
  "ma20": 960.0,
  "ma60": 940.0,
  "rsi_14": 68.5,
  "bias_20": 1.56,
  "volume_ratio": 1.23,
  "close_price": 985.0,
  "institutional": {
    "foreign_net": 12500,
    "trust_net": 3200,
    "dealer_net": -800
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

#### Table 3：`stock_raw_data`（原始數據表）

```sql
CREATE TABLE stock_raw_data (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(20)     NOT NULL,
    record_date     DATE            NOT NULL,
    technical       JSONB,          -- 技術面：K線、MA、RSI、量比
    institutional   JSONB,          -- 籌碼面：外資、投信、自營
    fundamental     JSONB,          -- 基本面：本益比、EPS、殖利率
    fetched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_raw_symbol_date
    ON stock_raw_data (symbol, record_date);

CREATE INDEX idx_raw_symbol      ON stock_raw_data (symbol);
CREATE INDEX idx_raw_record_date ON stock_raw_data (record_date);
CREATE INDEX idx_raw_technical_gin  ON stock_raw_data USING GIN (technical);
CREATE INDEX idx_raw_institutional_gin ON stock_raw_data USING GIN (institutional);
```

**欄位說明**

| 欄位            | 說明                                                    |
| --------------- | ------------------------------------------------------- |
| `technical`     | JSONB：ma5/ma20/ma60/rsi_14/volume_ratio/close_price 等 |
| `institutional` | JSONB：foreign_net/trust_net/dealer_net                 |
| `fundamental`   | JSONB：pe_ratio/eps/dividend_yield 等                   |
| `fetched_at`    | 資料抓取時間，用於判斷資料新鮮度                        |

**資料定位**

- `stock_raw_data` 是兩支分析 API 共用的股票原始資料來源（source of truth）。
- 技術面、籌碼面、基本面等完整 payload 應寫入這張表，而不是寫入 `stock_analysis_cache`。
- 若 `/analyze/position` 與 `/analyze` 在同一交易日查詢同一股票，應優先共用這張表中的原始資料，避免重複抓取。

**寫入策略（Phase 7 實作）**

`stock_raw_data` 的寫入由兩支分析端點在 `graph.invoke` 完成後順帶寫入，不依賴 n8n 主動抓取：

- `technical` ← `graph result["snapshot"]`（yfinance 技術面）
- `institutional` ← `graph result["institutional_flow"]`（籌碼面）
- `fundamental` ← `graph result["fundamental_data"]`（基本面）

n8n cron 的 `POST /internal/fetch-raw-data` 端點保留作為未來批次預拉的入口，但 Phase 7 不實作其內部邏輯（stub）。L2 共用邏輯（raw data 命中時跳過 graph 執行）為 Phase 8 前置工作。

---

#### Table 4：`stock_analysis_cache`（分析結果快取表）

```sql
CREATE TABLE stock_analysis_cache (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20)     NOT NULL,
    record_date         DATE            NOT NULL,
    signal_confidence   NUMERIC(5, 2),
    action_tag          VARCHAR(20),
    recommended_action  TEXT,
    indicators          JSONB,          -- 分析當下的指標快照（從 stock_raw_data 複製）
    final_verdict       TEXT,
    prev_action_tag     VARCHAR(20),
    prev_confidence     NUMERIC(5, 2),
    is_final            BOOLEAN         NOT NULL DEFAULT FALSE,  -- FALSE=盤中非定稿；TRUE=收盤定稿
    full_result         JSONB,                              -- 完整 AnalyzeResponse 快照，供 L1 快取命中時還原完整回應
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_cache_symbol_date
    ON stock_analysis_cache (symbol, record_date);

CREATE INDEX idx_cache_symbol      ON stock_analysis_cache (symbol);
CREATE INDEX idx_cache_record_date ON stock_analysis_cache (record_date);
CREATE INDEX idx_cache_action_tag  ON stock_analysis_cache (action_tag);
CREATE INDEX idx_cache_indicators_gin
    ON stock_analysis_cache USING GIN (indicators);
```

**欄位說明**

| 欄位                                  | 說明                                                                                                       |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `symbol` + `record_date`              | 唯一鍵，跨使用者共用，同一股票同一天只有一筆                                                               |
| `indicators`                          | 分析當下的指標快照，從 `stock_raw_data` 複製，保留分析時的數據狀態；只存分析所需摘要，不存完整原始 payload |
| `prev_action_tag` / `prev_confidence` | 前一交易日的訊號與信心分數，用於訊號轉向偵測                                                               |
| `is_final`                            | `FALSE` = 盤中非定稿（指標未收定，報告需附免責聲明）；`TRUE` = 收盤後定稿，權重高於盤中快照                |
| `full_result`                         | 完整 `AnalyzeResponse` JSONB 快照，確保 L1 快取命中時可回傳與首次分析相同的完整欄位                        |
| `updated_at`                          | 同一天若重新分析，記錄最新更新時間                                                                         |

**資料定位**

- `stock_analysis_cache` 是分析結果快取，不是股票原始資料主表。
- 這張表應保存信心分數、動作標籤、建議文本、可支撐歷史比較的指標快照，以及供 API 直接還原回應的 `full_result`。
- `full_result` 的內容是序列化後的完整 `AnalyzeResponse`，用途是保證 L1 快取命中時的 response fidelity，而不是充當原始資料倉儲。
- 籌碼面、基本面若需要完整欄位，應回到 `stock_raw_data. institutional / fundamental` 讀取；不應把完整 JSON 重複塞進 `stock_analysis_cache`。

---

## 3. API 架構：雙軌分析模式

### 3.1 設計原則

持股分析拆分為兩隻獨立 API，職責清晰分離：

|          | 歷史分析 API                            | 即時分析 API                               |
| -------- | --------------------------------------- | ------------------------------------------ |
| 端點     | `GET /portfolio/{portfolio_id}/history` | `POST /analyze/position` + `POST /analyze` |
| 用途     | 持倉列表展示每日診斷結果                | 使用者主動查詢任意個股                     |
| 資料來源 | `daily_analysis_log`（純 DB 查詢）      | 即時呼叫 LangGraph（LLM + 爬蟲）           |
| 觸發時機 | 前端載入持倉列表 / 點開個股詳情         | 使用者在即時分析視窗手動發起               |
| 成本     | 低（DB read）                           | 高（LLM token + 外部資料抓取）             |

### 3.2 使用者操作流程

```
即時分析視窗 ──查詢──▶ POST /analyze/position
      │                    │
      │                    ├─ 快取命中 → 直接回傳
      │                    └─ 快取未命中 → 抓數據/用現有數據打 model → 存快取 → 回傳
      │
      │ 想追蹤 → 加入持倉（POST /portfolio）
    │
    └─ 持倉列表初始化（GET /portfolio）
      ▼
持倉列表 ──展示──▶ GET /portfolio/{id}/history（查 daily_analysis_log，含 user_id）
      ▲
      │ 使用者查詢時若有持倉，分析結果同步寫入 daily_analysis_log
      │
      │ 每日 18:30 cron（抓原始數據，不打 model）
      └─ n8n 更新 stock_raw_data
```

**關鍵行為**：

- **收盤前 / cron 空窗期 / 非交易日**：持倉列表顯示最近一筆歷史分析紀錄，無需特殊判斷邏輯
- **分析快取跨使用者共用**：`stock_analysis_cache` 不含 `user_id`，同一股票同一天的分析結果全體使用者共享，不重複燒 LLM token
- **原始股票資料跨端點共用**：`/analyze/position` 與 `/analyze` 應優先讀取 `stock_raw_data` 的同日資料，共用技術面 / 籌碼面 / 基本面 payload
- **持倉寫回**：即時分析完成後，若使用者有該股的 active 持倉，同步寫一筆至 `daily_analysis_log`（含 `user_id`），列表頁立即可見

### 3.3 即時分析三段式快取邏輯

```
使用者查詢 symbol X
    │
    ▼
[1] stock_analysis_cache 有 record_date = today 的紀錄？
    ├─ YES，且 is_final = TRUE ──▶ 直接回傳（毫秒級，定稿數據）
    ├─ YES，且 is_final = FALSE，且現在 < 13:30 ──▶ 直接回傳（盤中快照，附免責聲明）
    ├─ YES，且 is_final = FALSE，且現在 ≥ 13:30 ──▶ 強制覆蓋（觸發 L2/L3，重新分析，is_final = TRUE）
    └─ NO
          ▼
    [2] stock_raw_data 有 record_date = today 的原始數據？
        ├─ YES（收盤後）──▶ 只打 model → 存 stock_analysis_cache（is_final=TRUE）→ 回傳（快）
        └─ NO（盤中）──▶ 爬蟲抓原始數據 + 打 model → 存兩張表（is_final=FALSE）→ 回傳（慢，現有行為）

分析完成後，若使用者有該股的 active 持倉，同步寫一筆到 daily_analysis_log（含 user_id）
```

**三段設計理念**：

| 層級         | 命中條件                          | 成本               | 說明                             |
| ------------ | --------------------------------- | ------------------ | -------------------------------- |
| L1：分析快取 | `stock_analysis_cache` 今日有紀錄 | 極低（DB read）    | 同天第二位使用者查同股，直接命中 |
| L2：原始數據 | `stock_raw_data` 今日有紀錄       | 中（只打 model）   | 收盤後 cron 已預拉，省去爬蟲等待 |
| L3：即時抓取 | 兩者皆無                          | 高（爬蟲 + model） | 盤中首次查詢，走現有流程         |

**`POST /analyze/position` / `POST /analyze` Response 皆包含 `is_final`**：

```json
{
  "symbol": "2330.TW",
  "signal_confidence": 68.5,
  "action_tag": "Hold",
  "recommended_action": "...",
  "final_verdict": "...",
  "is_final": false,
  "intraday_disclaimer": "目前為盤中即時分析，指標尚未收定，僅供參考。"
}
```

- `is_final = false`：前端應顯示盤中警告標語（黃色 banner 或角標），防止使用者將盤中數據誤認為收盤定論
- `is_final = true`：正常顯示，無需附加警告
- `intraday_disclaimer`：僅在 `is_final = false` 時回傳，`is_final = true` 時此欄位不存在（或為 `null`）

### 3.4 歷史分析 API 規格

```
GET /portfolio/{portfolio_id}/history?limit=20&offset=0
```

**Response**：

```json
{
  "symbol": "2330.TW",
  "total": 45,
  "records": [
    {
      "record_date": "2026-03-10",
      "signal_confidence": 72.5,
      "action_tag": "Hold",
      "recommended_action": "持續觀察，均線多頭排列",
      "indicators": { "rsi_14": 68.5, "ma5": 975.0, ... },
      "final_verdict": "...",
      "prev_action_tag": "Hold",
      "prev_confidence": 61.5
    }
  ]
}
```

**查詢邏輯**：從 `daily_analysis_log` 依 `symbol` + `user_id` 篩選，按 `record_date DESC` 排序，支援分頁。

### 3.5 即時分析寫回 DB 邏輯

`POST /analyze/position` 與 `POST /analyze` 完成分析後，依序執行兩段寫入：

```python
is_final = datetime.now().time() >= MARKET_CLOSE  # 13:30 後為定稿

# 1. 永遠寫入 stock_analysis_cache（跨使用者共用快取）
await upsert_analysis_cache(db, {
    "symbol":             payload.symbol,
    "signal_confidence":  result.get("signal_confidence"),
    "action_tag":         result.get("action_plan_tag"),
    "recommended_action": result.get("recommended_action"),
    "indicators":         _extract_indicators(result),
    "final_verdict":      result.get("analysis"),
    "is_final":           is_final,
})

# 2. 若使用者有該股的 active 持倉，額外寫入 daily_analysis_log
if has_active_portfolio(current_user.id, payload.symbol, db):
    await upsert_analysis_log(db, {
        "user_id":  current_user.id,
        "symbol":   payload.symbol,
        "is_final": is_final,
        ...
    })
```

- `stock_analysis_cache` 以 `(symbol, record_date)` 為衝突鍵，**不含 `user_id`**，跨使用者共用
- `daily_analysis_log` 以 `(user_id, symbol, record_date)` 為衝突鍵，只在使用者有持倉時寫入

### 3.7 Phase 7 缺口修補（2026-03-12）正式需求

以下需求為 Phase 7 驗收前的必要條件（MUST），不得延後至 Phase 8：

1. **AnalyzeResponse 契約補齊**
   - `POST /analyze/position` 與 `POST /analyze` 的 response model 必須包含 `is_final` 與 `intraday_disclaimer`。
   - `is_final = FALSE` 時，必須回傳 `intraday_disclaimer`；`is_final = TRUE` 時可為 `null`。

2. **雙端點快取一致性**
   - `POST /analyze/position` 與 `POST /analyze` 必須同時接入 L1 快取命中判斷（`get_analysis_cache` + `_handle_cache_hit`）。
   - 命中 L1 快取時，必須優先由 `stock_analysis_cache.full_result` 還原完整 `AnalyzeResponse`，回傳內容應與首次分析結果在欄位層級保持一致。
   - 若舊資料尚無 `full_result`，才允許 fallback 為最小相容回應；此 fallback 僅作為歷史資料相容，不得作為新寫入策略。

3. **分析寫回一致性**
   - 兩支分析端點完成分析後，必須 UPSERT 到 `stock_analysis_cache`。
   - 若使用者具 active 持倉，必須額外 UPSERT 到 `daily_analysis_log`。

4. **原始資料與分析快取分層**
   - 技術面、籌碼面、基本面完整資料必須以 `stock_raw_data` 為唯一共用來源。
   - `stock_analysis_cache` 僅保存分析結果與指標摘要快照，不作為完整原始資料倉儲。

5. **歷史上下文資料來源修正**
   - `history_loader.py` 查詢來源必須為 `stock_analysis_cache`，不得查 `daily_analysis_log`。
   - 兩支分析端點在 `graph.invoke` 前，若發現昨日 `stock_analysis_cache` 為 `is_final = FALSE`，必須先補抓昨日收盤技術指標並將該筆快取回填為 `is_final = TRUE`，再供 `history_loader.py` 讀取。

6. **持倉列表 API 補齊**
   - 後端必須提供 `GET /portfolio`，僅回傳目前使用者 `is_active = TRUE` 持倉。

7. **前端最小功能閉環**
   - `/analyze` 頁必須具備「加入我的持股」按鈕、建立持倉 Modal、盤中警示 banner。
   - 「我的持股」tab 必須改為持倉列表頁（非 PositionPage 輸入頁）。

### 3.6 兩段式快取機制：盤中 vs. 收盤後（Refined Cache Logic）

> **核心原則：收盤前數據皆不可信。** 盤中指標（RSI、MA、量比等）尚未收定，分析結論僅供參考，不得作為歷史復盤的依據。

#### 階段定義

| 階段           | 時間範圍      | `is_final` | 快取行為                                                                                     |
| -------------- | ------------- | ---------- | -------------------------------------------------------------------------------------------- |
| **盤中階段**   | 09:00 – 13:30 | `FALSE`    | 當日首位查詢者產生「非定稿」快照，後續同時段查詢直接命中，**不重複分析**（降低數據波動雜訊） |
| **收盤後階段** | 13:30 之後    | `TRUE`     | 任何新查詢（或 n8n 定時任務）**強制覆蓋**盤中紀錄，寫入定稿版本                              |

#### API 層判斷邏輯（`POST /analyze/position` / `POST /analyze` L1 檢查）

```python
from datetime import datetime, time

MARKET_CLOSE = time(13, 30)

cache = await get_analysis_cache(db, symbol, today)

if cache:
    if cache.is_final:
        return cache  # L1 命中：定稿數據，直接回傳
    if datetime.now().time() < MARKET_CLOSE:
        return cache  # L1 命中：盤中非定稿，直接回傳（附免責聲明）
    # 收盤後發現非定稿快取 → 強制觸發 L2/L3 重新分析
    pass  # fall through to L2/L3

# L2 / L3 分析流程（兩支端點共用相同快取決策）
result = await run_analysis(symbol, db)
is_final = datetime.now().time() >= MARKET_CLOSE
await upsert_analysis_cache(db, symbol, result, is_final=is_final)
```

#### 免責聲明注入規則

- `is_final = FALSE` 的分析結果，`final_verdict` 開頭自動附加：
  ```
  ⚠️ 注意：目前為盤中階段（指標未收定），以下分析僅供即時參考，不代表當日收盤定論。
  ```
- `is_final = TRUE` 的結果為收盤定稿，作為歷史復盤與勝率回測的唯一依據。

#### `is_final` 與三段式快取（L1–L3）的整合關係

```
L1 命中
 ├─ is_final=TRUE  → 直接回傳（無論何時）
 ├─ is_final=FALSE + 盤中 → 直接回傳（附免責聲明）
 └─ is_final=FALSE + 收盤後 → 作廢，強制重走 L2/L3，完成後以 is_final=TRUE 覆蓋

L2 命中（stock_raw_data 有今日原始數據）
 └─ 只打 model → is_final=TRUE（收盤後一定是定稿）

L3 觸發（盤中首次查詢）
 └─ 爬蟲 + model → is_final=FALSE（指標未收定）
```

---

## 4. n8n 自動化工作流設計

### 4.1 架構總覽

```
── Zeabur 專案（同一內網）──────────────────────────────┐
│                                                        │
│  n8n                                                   │
│      └─── 每日數據更新流 (Cron: 每日 18:30)            │
│               └─── 收集 watchlist → 批次抓原始數據     │
│                        → HTTP POST /internal/fetch-raw-data
│                                                        │
│  FastAPI Backend (AI Stock Sentinel)                   │
│      ├── POST /analyze/position  （三段式快取邏輯）     │
│      └── POST /internal/fetch-raw-data（n8n cron 呼叫）│
│                                                        │
│  PostgreSQL (Zeabur)                                   │
│      ├── user_portfolio                                │
│      ├── stock_raw_data          ← 原始數據快取        │
│      ├── stock_analysis_cache    ← 分析結果快取（跨使用者共用）
│      └── daily_analysis_log      ← 持倉歷史（含 user_id）
│                                                        │
└────────────────────────────────────────────────────────┘
```

**傳輸效率**：n8n、FastAPI、PostgreSQL 同在 Zeabur 內網，數據交換不經公網，無額外隧道建立開銷，延遲最低且安全性最高。

---

### 4.2 工作流 A：每日數據更新流

**觸發條件**：Cron 表達式 `30 18 * * 1-5`（台灣時間，週一至週五收盤後）

**定位調整**：此流程**不再批次打 model**，改為預拉原始數據存入 `stock_raw_data`，讓 model 推理只在使用者主動查詢時才觸發。

**完整節點設計**

```
[Cron Trigger] 30 18 * * 1-5
    │
    ▼
[Postgres Node] 收集 watchlist
    Query:
        SELECT DISTINCT symbol FROM user_portfolio WHERE is_active = TRUE
        UNION
        SELECT DISTINCT symbol FROM stock_analysis_cache
        WHERE record_date >= CURRENT_DATE - 30  -- 近 30 天被查過的
    │
    ▼
[Split In Batches] ── 每批 1 筆
    │
    ▼
[HTTP Request Node] ── 呼叫後端原始數據抓取端點
    POST /internal/fetch-raw-data
    Body: { "symbol": "{{ $json.symbol }}", "date": "today" }
    │
    ▼
[Wait Node] ── 1 秒（避免外部 API 速率限制）
```

---

### 4.3 n8n 批次寫入規範（Network Efficiency）

> **背景**：n8n 與 PostgreSQL 同在 Zeabur 內網，雖然無隧道開銷，逐筆（row-by-row）`INSERT` 仍會造成不必要的往返次數。批次合併寫入是基本的效能規範。

#### 批次寫入規範

**禁止做法**：在 n8n 的迴圈中對 `stock_raw_data` 逐筆執行 `INSERT`：

```sql
-- ❌ 禁止：每筆一次 INSERT，100 筆 = 100 次往返
INSERT INTO stock_raw_data (symbol, record_date, technical) VALUES ('2330.TW', '2026-03-12', '{}');
INSERT INTO stock_raw_data (symbol, record_date, technical) VALUES ('2454.TW', '2026-03-12', '{}');
-- ...
```

**正確做法**：在 n8n 使用 **Execute Query 節點**，將多筆資料合併為單一 `INSERT ... VALUES` 語句：

```sql
-- ✅ 正確：N 筆合一次 INSERT
INSERT INTO stock_raw_data (symbol, record_date, technical, institutional, fetched_at)
VALUES
    ('2330.TW', '2026-03-12', '{"rsi_14": 68.5, "close_price": 985.0}'::JSONB, '{}'::JSONB, NOW()),
    ('2454.TW', '2026-03-12', '{"rsi_14": 55.2, "close_price": 1230.0}'::JSONB, '{}'::JSONB, NOW()),
    ('2317.TW', '2026-03-12', '{"rsi_14": 72.1, "close_price": 118.5}'::JSONB, '{}'::JSONB, NOW())
ON CONFLICT (symbol, record_date) DO UPDATE
    SET technical   = EXCLUDED.technical,
        institutional = EXCLUDED.institutional,
        fetched_at  = EXCLUDED.fetched_at;
```

#### n8n 節點設計要點

| 設定項             | 建議值                         | 原因                                                          |
| ------------------ | ------------------------------ | ------------------------------------------------------------- |
| **節點類型**       | Execute Query（非 Insert Row） | Insert Row 為逐筆操作，必須改用 Execute Query 組合多值 INSERT |
| **Wait Node** 間隔 | 1 秒（每批次後）               | 避免對外部資料來源（yfinance 等）造成速率限制                 |

#### 工作流 A 節點更新

Section 4.2 的 `[Split In Batches]` 設定調整：

- 原設定：每批 1 筆（適合 HTTP Request 節點的速率控制）
- **原始數據寫入路徑**：改為每批 50 筆 → Function 節點組裝多值 INSERT → Execute Query 節點送出

---

## 5. 前端頁面架構

### 5.1 頁面總覽

| 頁面     | 路由         | 主要 API                                         | 說明                             |
| -------- | ------------ | ------------------------------------------------ | -------------------------------- |
| 個股分析 | `/analyze`   | `POST /analyze/position` + `POST /analyze`       | 輸入代碼查詢即時分析，可加入持倉 |
| 我的持股 | `/portfolio` | `GET /portfolio` + `GET /portfolio/{id}/history` | 持倉列表 + 個股歷史診斷紀錄      |

### 5.2 個股分析頁（`/analyze`）

```
┌─────────────────────────────────────┐
│  輸入股票代碼  [2330.TW]  [分析]    │
├─────────────────────────────────────┤
│  ⚠️ 盤中即時分析，指標尚未收定      │  ← is_final=false 時顯示
├─────────────────────────────────────┤
│  Hold  信心分數：68.5               │
│  建議：持續觀察，均線多頭排列...    │
│                                     │
│  [加入我的持股]                     │  ← 點擊開啟填寫 Modal
└─────────────────────────────────────┘
```

**「加入我的持股」Modal 欄位**：

| 欄位     | 對應 `POST /portfolio` 欄位 | 說明               |
| -------- | --------------------------- | ------------------ |
| 股票代碼 | `symbol`                    | 自動帶入，不可修改 |
| 成本價   | `entry_price`               | 必填，購入均價     |
| 持有股數 | `quantity`                  | 必填               |
| 購入日期 | `entry_date`                | 必填，預設今日     |
| 備註     | `notes`                     | 選填               |

**互動邏輯**：

- 若該股已在 active 持倉中，按鈕改為「已追蹤」（disabled）
- 新增成功後按鈕變為「已追蹤」，不跳頁

### 5.3 我的持股頁（`/portfolio`）

```
┌─────────────────────────────────────┐
│  我的持股                           │
├─────────────────────────────────────┤
│  2330.TW  台積電                    │
│  成本 850 | 現價 985 | Hold 72.5   │  ← 最新一筆 daily_analysis_log
│  [查看歷史]                         │
├─────────────────────────────────────┤
│  2454.TW  聯發科                    │
│  成本 1100 | 現價 1230 | Trim 65.0 │
│  [查看歷史]                         │
└─────────────────────────────────────┘
```

**「查看歷史」展開後**（呼叫 `GET /portfolio/{id}/history`）：

```
┌─────────────────────────────────────┐
│  2330.TW 診斷歷史                   │
├────────────┬───────┬───────────────┤
│ 日期       │ 建議  │ 信心分數      │
├────────────┼───────┼───────────────┤
│ 2026-03-12 │ Hold  │ 72.5          │
│ 2026-03-11 │ Hold  │ 61.5          │
│ 2026-03-10 │ Trim  │ 74.0          │
└────────────┴───────┴───────────────┘
```

**持倉上限提示**：active 持倉已達 5 筆時，「加入我的持股」按鈕顯示 tooltip「最多追蹤 5 筆持股」並 disabled。

---

## 6. 邏輯升級與模型優化 Roadmap

### 6.1 歷史對比敘事（API 層升級）

**目標**：`POST /analyze/position` 與 `POST /analyze` 的 `final_verdict` 能包含「訊號轉向分析」，而非每次都是孤立的當日診斷。

**實作方式**（搭配 Section 3.5 的即時分析寫回機制）：

1. API 接收請求後，在呼叫 LangGraph 前，先檢查昨日快取是否為盤中未定稿；若是，先補抓昨日收盤技術指標並更新 `stock_analysis_cache.indicators` 與 `is_final=TRUE`：

```python
backfill_yesterday_indicators(db, symbol)
```

2. 完成補正後，再執行 DB 查詢取得昨日數據：

```python
# backend/services/history_loader.py
# 查詢來源：stock_analysis_cache（跨使用者共用），不查 daily_analysis_log。
# 原因：daily_analysis_log 只有持倉使用者才有紀錄，非持倉查詢會取不到昨日上下文。

async def load_yesterday_context(symbol: str, db: AsyncSession) -> dict | None:
    """從 stock_analysis_cache 讀取昨日分析結果，作為 LLM 的歷史上下文。"""
    result = await db.execute(
        select(StockAnalysisCache)       # 查 stock_analysis_cache，非 daily_analysis_log
        .where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == date.today() - timedelta(days=1)
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {
        "prev_action_tag":   row.action_tag,
        "prev_confidence":   float(row.signal_confidence) if row.signal_confidence else None,
        "prev_rsi":          (row.indicators or {}).get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(row.indicators),
    }
```

3. 將歷史上下文注入 LLM Prompt，在 `final_verdict` 段落加入轉向說明：

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

### 6.2 策略勝率校準（`confidence_scorer.py` 動態調權）

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

## 7. 實作任務與優先序

### Phase 7 前置：使用者系統建置（優先執行）

> 詳細設計見 `docs/plans/2026-03-10-google-oauth-user-system.md`

| 優先序 | 任務                               | 說明                                            | 預估工時 |
| ------ | ---------------------------------- | ----------------------------------------------- | -------- |
| P0     | PostgreSQL 部署 + `users` 表 DDL   | 認證系統的資料基礎                              | 0.5 天   |
| P0     | `POST /auth/google` 端點           | 驗證 Google id_token，建立/查找使用者，簽發 JWT | 0.5 天   |
| P0     | JWT 簽發與驗證（`jwt_handler.py`） | 所有認證的基礎模組                              | 0.5 天   |
| P1     | `GET /auth/me` 端點                | 前端初始化使用者狀態                            | 0.25 天  |
| P1     | `get_current_user` Depends         | 保護現有 `/analyze/*` 路由                      | 0.25 天  |
| P1     | 前端登入頁 + Google OAuth SDK 整合 | 使用者操作入口                                  | 0.5 天   |
| P1     | 前端認證狀態管理 + API 攔截器      | JWT 自動帶入所有請求                            | 0.5 天   |

### Phase 7：基礎設施建置（本文件範圍）

| 優先序 | 任務                                                | 說明                                                                                       | 預估工時 |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------ | -------- |
| P0     | 執行 Table Schema 建立                              | 執行本文件 Section 2 的 DDL（含 user_id FK）                                               | 0.5 天   |
| P1     | `stock_raw_data` + `stock_analysis_cache` Table DDL | 新增兩張表（Section 2）                                                                    | 0.25 天  |
| P1     | SQLAlchemy 接入（FastAPI）                          | 建立 `User`、`DailyAnalysisLog`、`StockRawData`、`StockAnalysisCache` ORM Model，實作 CRUD | 1 天     |
| P1     | 三段式快取邏輯實作                                  | `POST /analyze/position` 與 `POST /analyze` 同步加入快取判斷（Section 3.3）                | 1 天     |
| P1     | 完整回應快取欄位補齊                                | `stock_analysis_cache` 新增 `full_result`，L1 命中時可還原完整 `AnalyzeResponse`           | 0.5 天   |
| P1     | `POST /internal/fetch-raw-data` 端點                | n8n 呼叫的數據抓取端點                                                                     | 0.5 天   |
| P1     | `history_loader.py` 實作                            | 昨日上下文讀取服務（Section 5.1）                                                          | 0.5 天   |
| P1     | 昨日未定稿快取補正                                  | 端點分析前自動 backfill 昨日收盤技術指標，避免歷史上下文讀到盤中快取                       | 0.5 天   |
| P1     | `GET /portfolio` 端點                               | 提供 active 持倉列表，供前端「我的持股」頁初始化（Section 3.7）                            | 0.5 天   |
| P1     | `GET /portfolio/{id}/history` 端點                  | 歷史分析 API，從 `daily_analysis_log` 讀取診斷紀錄（Section 3.4）                          | 0.5 天   |
| P1     | 即時分析結果寫回 DB                                 | 兩支分析端點完成後依序寫入 `stock_analysis_cache` 與 `daily_analysis_log`（Section 3.5）   | 0.5 天   |
| P1     | n8n 每日數據更新流部署                              | 建立 Workflow A（Section 4.2）                                                             | 1 天     |
| P2     | 安全隧道設定（Cloudflare Tunnel）                   | 保護 n8n 至本地 DB 的連線                                                                  | 0.5 天   |

### Phase 8：邏輯強化（後續規劃）

| 任務             | 說明                                                                                                             |
| ---------------- | ---------------------------------------------------------------------------------------------------------------- |
| 訊號轉向敘事     | API 整合 `history_loader`，Prompt 升級                                                                           |
| 勝率回測腳本     | 引入 yfinance 歷史價格，比對診斷準確率                                                                           |
| 信心分數校準     | 基於回測結果的半自動調權流程                                                                                     |
| 資料抓取併發優化 | `crawl` 節點改用 `asyncio.gather` 同時抓取技術面（yfinance）與籌碼面（institutional flow），縮短整體分析等待時間 |

### Phase 9：平台化（長期願景）

| 任務           | 說明                             |
| -------------- | -------------------------------- |
| 前端復盤儀表板 | 視覺化信心分數時序、訊號轉向歷史 |

---

## 8. 關鍵設計決策記錄

| 決策                        | 選擇                                               | 理由                                                                  |
| --------------------------- | -------------------------------------------------- | --------------------------------------------------------------------- |
| 分析 API 架構               | 雙軌模式（歷史 + 即時）                            | 列表頁純 DB 查詢不燒 token；即時分析保持現有能力；職責清晰無判斷邏輯  |
| 即時分析快取策略            | 三段式快取（分析快取 → 原始數據 → 即時抓取）       | 最大化快取命中率，只在必要時才燒 LLM token                            |
| 快取回應保真策略            | `stock_analysis_cache.full_result`                 | L1 命中時回傳完整 `AnalyzeResponse`，避免快取命中與首次分析欄位不一致 |
| 分析快取 user_id            | 不含 user_id（跨使用者共用）                       | 同一股票同一天的分析結果對所有使用者相同，無需重複計算                |
| n8n cron 定位               | 原始數據預抓取，不打 model                         | model 推理只在有人查詢時才觸發，避免無效燒 token                      |
| 原始數據更新頻率            | 收盤後每日一次                                     | 系統定位為收盤後復盤，不需盤中即時數據                                |
| 昨日快取邊界修正            | 分析前自動 backfill 昨日未定稿指標                 | 確保 `history_loader` 讀到的是收盤數據，而非前一日盤中快照            |
| 即時分析寫回 DB             | 交易日自動 UPSERT                                  | 使用者加入持倉後列表立即有資料，不需等隔天 cron                       |
| `daily_analysis_log` 唯一鍵 | `(user_id, symbol, record_date)`                   | 不同使用者可各自擁有同一股票的分析紀錄                                |
| DB 型別                     | PostgreSQL（JSONB）                                | JSONB GIN 索引支援指標值的高效查詢                                    |
| 自動化引擎                  | n8n                                                | 低代碼、支援 Postgres Node、Webhook、排程，適合小型量化平台           |
| 部署環境                    | Zeabur 全雲端（n8n + FastAPI + PostgreSQL 同專案） | 內網通訊零公網暴露，延遲最低，無需安全隧道                            |
| 歷史數據注入                | DB 查詢後傳入 Prompt                               | 嚴守 Tool Use 原則，LLM 不猜歷史數值                                  |
| 權重校準流程                | 人工審核後調整                                     | 防止自動調權引入系統性偏差                                            |
| 出場後的倉位處理            | `is_active = FALSE`（軟刪除）                      | 保留歷史診斷 log 的可追溯性                                           |
| 使用者認證方式              | Google OAuth + JWT                                 | 對邀請制使用者友善，後端無狀態易擴展                                  |
| 使用者識別碼                | `google_sub`（非 email）                           | email 可被使用者更改，`sub` 是 Google 的不可變 ID                     |
| 帳號刪除策略                | 軟刪除（`deleted_at`）                             | 歷史 log 去識別化後保留，供模型優化使用                               |
| `user_id` FK 可空性         | Nullable + `ON DELETE SET NULL`                    | 使用者刪帳號時 log 保留（user_id 設 NULL），不破壞歷史資料完整性      |
| 資料隔離模式                | 持倉隔離、查詢歷史聚合                             | 個人持倉各自獨立，跨使用者的診斷 log 聚合用於模型優化                 |

---

_文件版本：v1.7 | 最後更新：2026-03-13 | 變更：補入 `stock_analysis_cache.full_result` 完整回應快取需求，以及昨日未定稿快取 backfill 正式規格；`stock_raw_data` 寫入策略已由分析端點順帶完成。_
