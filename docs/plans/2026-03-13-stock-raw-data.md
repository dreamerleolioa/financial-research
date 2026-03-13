> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

# Stock Raw Data 寫入計劃

**目標：** 實作 `fetch_and_store_raw_data`，讓兩支分析端點在 `graph.invoke` 完成後，將當次抓到的技術面（`snapshot`）、籌碼面（`institutional_flow`）、基本面（`fundamental_data`）寫入 `stock_raw_data` 資料表（UPSERT，同日只存一筆）。本計劃不實作 L2 共用邏輯（raw data 命中時跳過 graph 執行）。

**Architecture：** `fetch_and_store_raw_data` 改為真正的 SQLAlchemy `text()` UPSERT，欄位對應 `technical ← snapshot`、`institutional ← institutional_flow`、`fundamental ← fundamental_data`。兩個 endpoint 在 `upsert_analysis_cache` 之後呼叫此函式。

**Tech Stack：** Python, SQLAlchemy (`text()` / JSONB), FastAPI, PostgreSQL

---

### Task 1：實作 `fetch_and_store_raw_data`

**目標：** 將目前是 `pass` stub 的 `fetch_and_store_raw_data` 改為真正執行 UPSERT，把 graph result 的原始資料存入 `stock_raw_data`。

**修改檔案：**
- `backend/src/ai_stock_sentinel/api.py`（約 line 612–618）

**Before（現有 stub）：**

```python
def fetch_and_store_raw_data(db: Session, symbol: str, record_date) -> None:
    """抓取技術面、籌碼面、基本面原始數據並 UPSERT 至 stock_raw_data。

    TODO: 接入現有爬蟲（yfinance_client、institutional_flow、fundamental）。
    目前為 stub，不執行任何操作。
    """
    pass
```

**After（新簽名與實作）：**

```python
def fetch_and_store_raw_data(
    db: Session,
    symbol: str,
    *,
    technical: dict | None,
    institutional: dict | None,
    fundamental: dict | None,
) -> None:
    """將 graph result 的原始資料 UPSERT 至 stock_raw_data（今日）。

    - technical      ← graph result["snapshot"]
    - institutional  ← graph result["institutional_flow"]
    - fundamental    ← graph result["fundamental_data"]

    使用 ON CONFLICT (symbol, record_date) DO UPDATE，同日只存一筆。
    若籌碼面含 'error' 鍵，仍寫入（保留原始錯誤資訊以供 debug）。
    """
    db.execute(
        text("""
            INSERT INTO stock_raw_data (
                symbol, record_date, technical, institutional, fundamental, fetched_at
            ) VALUES (
                :symbol, CURRENT_DATE,
                CAST(:technical AS jsonb),
                CAST(:institutional AS jsonb),
                CAST(:fundamental AS jsonb),
                NOW()
            )
            ON CONFLICT (symbol, record_date) DO UPDATE SET
                technical     = EXCLUDED.technical,
                institutional = EXCLUDED.institutional,
                fundamental   = EXCLUDED.fundamental,
                fetched_at    = NOW()
        """),
        {
            "symbol":        symbol,
            "technical":     json.dumps(technical or {}),
            "institutional": json.dumps(institutional or {}),
            "fundamental":   json.dumps(fundamental or {}),
        }
    )
    db.commit()
```

同時更新 `/internal/fetch-raw-data` endpoint 中舊有的呼叫（簽名已改變），將原本的 `fetch_and_store_raw_data(db, payload.symbol, record_date)` 改為 `pass`，因為這個 endpoint 的主動抓取設計已由「分析完成後順帶寫入」取代。

**驗收條件：**
- 呼叫後，`stock_raw_data` 中出現對應 `(symbol, CURRENT_DATE)` 的一筆資料
- 欄位 `technical` 包含 `current_price`、`recent_closes` 等 yfinance 欄位
- 欄位 `institutional` 包含 `foreign_net`、`flow_label` 等（或含 `error` key 的失敗記錄）
- 欄位 `fundamental` 包含 `pe_ratio`、`pb_ratio` 等（或含 `error` key 的失敗記錄）
- 同日第二次呼叫不新增資料列，只更新（UPSERT）

---

### Task 2：在兩支分析端點呼叫 `fetch_and_store_raw_data`

**目標：** 在 `/analyze` 與 `/analyze/position` 的 `graph.invoke` 成功路徑中，於 `upsert_analysis_cache` 之後呼叫 `fetch_and_store_raw_data`。

**修改檔案：**
- `backend/src/ai_stock_sentinel/api.py`（`/analyze` endpoint）
- `backend/src/ai_stock_sentinel/api.py`（`/analyze/position` endpoint）

在兩個 endpoint 的 `upsert_analysis_cache(...)` 呼叫之後，各加入：

```python
    fetch_and_store_raw_data(
        db,
        payload.symbol,
        technical=result.get("snapshot"),
        institutional=result.get("institutional_flow"),
        fundamental=result.get("fundamental_data"),
    )
```

**驗收條件：**
- 呼叫 `POST /analyze` 後，`stock_raw_data` 出現對應今日的資料列
- 呼叫 `POST /analyze/position` 後，同樣出現（若 `/analyze` 同日已執行，為 UPSERT 更新）
- 現有端點 response 結構不變

---

### 注意事項：`indicators` 與 `stock_raw_data` 並存

`stock_analysis_cache.indicators` 存的是 `_extract_indicators()` 的**壓縮摘要**（ma5/ma20/ma60/rsi_14/close_price/volume_ratio/institutional 幾個數字），供 `history_loader.py` 快速讀取昨日上下文（prev_rsi、prev_ma_alignment）用，**不應移除**。

`stock_raw_data` 存的是**完整原始 payload**，供 Phase 8 的跨日分析與 L2 共用邏輯使用。

兩者用途不同，應並存。

---

### Commit 建議

```bash
# Task 1 完成後
git add backend/src/ai_stock_sentinel/api.py
git commit -m "feat: implement fetch_and_store_raw_data to upsert stock_raw_data"

# Task 2 完成後
git add backend/src/ai_stock_sentinel/api.py
git commit -m "feat: call fetch_and_store_raw_data after graph.invoke in both analyze endpoints"
```
