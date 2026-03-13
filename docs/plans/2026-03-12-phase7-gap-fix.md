# Phase 7 缺口修補計劃

> 日期：2026-03-12  
> 目標：補完 Phase 7 spec 中已設計但尚未實作的部分  
> 估計工時：3–4 小時  
> 文件定位：Phase 7 需求補丁（Requirement Addendum）  
> 文件狀態：Approved for Implementation

---

## 文件用途與範圍

- 本文件為 Phase 7 主規格的補丁需求，內容屬於 MUST 規格，不是可選 TODO。
- 本文件聚焦需求與驗收條件，不承擔進度追蹤職責。
- 本文件優先處理 API 契約缺口、快取落地、持倉頁閉環，不擴充 Phase 8 功能。

---

## 現況快照（As-Is）

| 項目                                                               | 狀態                                                                       |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| DB ORM Model（4 張表）                                             | ✅ 已完成                                                                  |
| `upsert_analysis_cache` 函式                                       | ✅ 已定義                                                                  |
| `_extract_indicators` 函式                                         | ✅ 已定義                                                                  |
| `get_analysis_cache` / `_handle_cache_hit` 函式                    | ✅ 已定義                                                                  |
| `history_loader.py`                                                | ✅ 已定義（但查的是 `DailyAnalysisLog`，spec 說應查 `StockAnalysisCache`） |
| `GET /portfolio/{id}/history` 端點                                 | ✅ 已完成                                                                  |
| `POST /portfolio` 端點                                             | ✅ 已完成                                                                  |
| **`/analyze/position`：呼叫 upsert_analysis_cache**                | ❌ 未接上                                                                  |
| **`/analyze/position`：L1 快取命中邏輯**                           | ❌ 未接上                                                                  |
| **`/analyze/position`：有持倉才寫 daily_analysis_log**             | ❌ 未實作                                                                  |
| **`/analyze`：同上三項**                                           | ❌ 未接上                                                                  |
| **`AnalyzeResponse` 缺少 `is_final` / `intraday_disclaimer` 欄位** | ❌ 未加入                                                                  |
| **前端個股分析頁：「加入我的持股」按鈕及 Modal**                   | ❌ 未實作                                                                  |
| **前端我的持股頁（portfolio list）**                               | ❌ 未實作                                                                  |
| `history_loader.py` 查詢來源錯誤                                   | ⚠️ 需修正（應查 `StockAnalysisCache`）                                     |

---

## Task 1：後端 — `AnalyzeResponse` 加入 `is_final` / `intraday_disclaimer`

**檔案**：`backend/src/ai_stock_sentinel/api.py`

`AnalyzeResponse` 目前缺少這兩個欄位，導致前端無法判斷是否為盤中數據。

```python
class AnalyzeResponse(BaseModel):
    # ... 現有欄位 ...
    is_final: bool = True                        # 新增
    intraday_disclaimer: str | None = None       # 新增
```

**驗收條件（MUST）**

- `POST /analyze/position` 與 `POST /analyze` response model 皆包含上述兩欄位。
- `is_final = false` 時，`intraday_disclaimer` 不可為空字串。
- `is_final = true` 時，`intraday_disclaimer` 可為 `null`。

---

## Task 2：後端 — `/analyze/position` 與 `/analyze` 接上快取邏輯

**檔案**：`backend/src/ai_stock_sentinel/api.py`

兩支端點都需要：

1. **L1 快取命中檢查**（呼叫 `get_analysis_cache` + `_handle_cache_hit`）
2. **L2 原始資料共用檢查**（呼叫 `get_raw_data`，優先共用同日 `stock_raw_data`）
3. **分析完成後寫入 `stock_analysis_cache`**（呼叫 `upsert_analysis_cache`）
4. **若使用者有 active 持倉，額外寫入 `daily_analysis_log`**

### Task 2 補充規格：`stock_raw_data` / `stock_analysis_cache` 分層

- `stock_raw_data` 是 `/analyze/position` 與 `/analyze` 共用的原始股票資料來源。
- 技術面、籌碼面、基本面完整 payload 應存於 `stock_raw_data`。
- `stock_analysis_cache` 僅保存分析結果與指標摘要快照，不保存完整原始資料。
- 若同日已存在 `stock_raw_data`，分析端點應優先重用，避免重複抓取技術面 / 籌碼面 / 基本面資料。

這兩支端點目前都沒有 `db: Session = Depends(get_db)` 參數，需要先加上。

### `/analyze/position` 改動後結構

```python
@app.post("/analyze/position", response_model=AnalyzeResponse)
def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    db: Session = Depends(get_db),           # 新增
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    now_time = datetime.now().time()

    # L1：快取命中檢查
    cache = get_analysis_cache(db, payload.symbol)
    if cache:
        hit = _handle_cache_hit(cache, now_time)
        if hit:
            # 快取命中時也需寫 daily_analysis_log（若有持倉）
            _maybe_upsert_log(db, current_user.id, payload.symbol, cache, now_time)
            return _build_response_from_cache(hit, payload)

    # L2：原始資料共用
    raw_data = get_raw_data(db, payload.symbol)
    if raw_data:
        # 使用 stock_raw_data 內已存在的 technical / institutional / fundamental
        # ... 走只打 model 的分析流程 ...
        pass

    # L3：正常走 LangGraph 分析流程
    # ... 現有 graph.invoke() 邏輯 ...

    # 寫入 stock_analysis_cache
    is_final = now_time >= MARKET_CLOSE
    upsert_analysis_cache(db, {
        "symbol":             payload.symbol,
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
        "is_final":           is_final,
    })

    # 若使用者有持倉，寫 daily_analysis_log
    _maybe_upsert_log_from_result(db, current_user.id, payload.symbol, result, is_final)

    response = _build_response(result)
    response.is_final = is_final
    response.intraday_disclaimer = INTRADAY_DISCLAIMER if not is_final else None
    return response
```

### 需要新增的輔助函式

```python
def has_active_portfolio(user_id: int, symbol: str, db: Session) -> bool:
    return db.execute(
        select(func.count()).select_from(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.symbol == symbol,
            UserPortfolio.is_active == True,
        )
    ).scalar() > 0


def upsert_analysis_log(db: Session, data: dict) -> None:
    """UPSERT 分析結果至 daily_analysis_log（含 user_id）。"""
    import json
    db.execute(
        text("""
            INSERT INTO daily_analysis_log (
                user_id, symbol, record_date, signal_confidence, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, is_final
            ) VALUES (
                :user_id, :symbol, CURRENT_DATE, :signal_confidence, :action_tag,
                :recommended_action, :indicators::jsonb, :final_verdict,
                (SELECT action_tag FROM daily_analysis_log
                 WHERE user_id = :user_id AND symbol = :symbol
                   AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM daily_analysis_log
                 WHERE user_id = :user_id AND symbol = :symbol
                   AND record_date = CURRENT_DATE - 1),
                :is_final
            )
            ON CONFLICT (user_id, symbol, record_date) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict,
                is_final           = EXCLUDED.is_final
        """),
        {
            "user_id":            data.get("user_id"),
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
            "is_final":           data.get("is_final", False),
        }
    )
    db.commit()
```

**驗收條件（MUST）**

- 兩支分析端點都具備 L1 快取命中判斷。
- 兩支分析端點都具備 L2 `stock_raw_data` 命中判斷，且同日資料可跨端點共用。
- 兩支分析端點都會在分析完成後寫入 `stock_analysis_cache`。
- 使用者有 active 持倉時，兩支分析端點都會寫入 `daily_analysis_log`。
- L1 命中時，回傳仍符合 `AnalyzeResponse` 形狀（允許部分欄位 `null`）。
- 完整技術面 / 籌碼面 / 基本面資料不寫入 `stock_analysis_cache`。

---

## Task 3：後端 — 修正 `history_loader.py` 查詢來源

**檔案**：`backend/src/ai_stock_sentinel/services/history_loader.py`

目前查的是 `DailyAnalysisLog`，但 spec 規定應查 `StockAnalysisCache`。

> 原因：`daily_analysis_log` 只有持倉使用者才有紀錄，非持倉查詢會取不到昨日上下文。  
> 正確來源：`stock_analysis_cache`（跨使用者共用）

```python
# 修正後：改 import 並改查 StockAnalysisCache
from ai_stock_sentinel.db.models import StockAnalysisCache

def load_yesterday_context(symbol: str, db: Session) -> dict | None:
    yesterday = date.today() - timedelta(days=1)
    result = db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == yesterday,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    indicators = row.indicators or {}
    return {
        "prev_action_tag":   row.action_tag,
        "prev_confidence":   float(row.signal_confidence) if row.signal_confidence is not None else None,
        "prev_rsi":          indicators.get("rsi_14"),
        "prev_ma_alignment": _derive_ma_alignment(indicators),
    }
```

**驗收條件（MUST）**

- `history_loader.py` 不再 import / 查詢 `DailyAnalysisLog`。
- `load_yesterday_context` 僅從 `StockAnalysisCache` 讀取昨日資料。

---

## Task 4：前端 — 個股分析頁加入「加入我的持股」功能

**檔案**：`frontend/src/App.tsx`

目前 `/analyze` 頁（`activeTab === "analyze"`）分析完成後沒有任何持股相關動作。

需要加入：

1. **持倉狀態檢查**：分析完成後（或頁面載入時），查詢 `GET /portfolio` 確認該 symbol 是否已在 active 持倉中
2. **「加入我的持股」按鈕**：
   - 顯示條件：有分析結果 (`result !== null`)
   - disabled 條件：該 symbol 已追蹤，或 active 持倉已達 5 筆
   - tooltip：已追蹤時顯示「已追蹤」；達上限時顯示「最多追蹤 5 筆持股」
3. **Modal**（點擊後彈出）：
   - 欄位：成本價（必填）、持有股數（必填）、購入日期（預設今日）、備註（選填）
   - symbol 自動帶入，唯讀
   - 送出：`POST /portfolio`，成功後按鈕變為「已追蹤」（disabled）
4. **盤中警告 banner**：`result.is_final === false` 時顯示黃色提示列

**驗收條件（MUST）**

- 分析完成後，畫面可判斷該 symbol 是否已在 active 持倉。
- 符合 disabled 條件時按鈕不可點擊，且顯示對應提示。
- Modal 欄位完整，送出成功後按鈕狀態更新為「已追蹤」。
- `is_final === false` 時，固定顯示盤中風險提示。

---

## Task 5：前端 — 「我的持股」頁面實作

**目前狀況**：點「我的持股」tab 會渲染 `<PositionPage />`，這是「持股診斷」輸入頁，**不是持倉列表**。

需要新建 `frontend/src/pages/PortfolioPage.tsx`，並在 `App.tsx` 的 tab 路由中替換。

### API 串接

- `GET /portfolio`：取得目前使用者的 active 持倉列表（目前後端尚未有此端點，需同步新增）
- `GET /portfolio/{id}/history`：點開某筆持倉時取得歷史診斷紀錄

### 後端需同步新增：`GET /portfolio`

**檔案**：`backend/src/ai_stock_sentinel/portfolio/router.py`

```python
@router.get("")
def list_portfolio(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(UserPortfolio).where(
            UserPortfolio.user_id == current_user.id,
            UserPortfolio.is_active == True,
        ).order_by(UserPortfolio.created_at.desc())
    ).scalars().all()

    return [
        {
            "id":           r.id,
            "symbol":       r.symbol,
            "entry_price":  float(r.entry_price),
            "quantity":     r.quantity,
            "entry_date":   r.entry_date.isoformat(),
            "notes":        r.notes,
        }
        for r in rows
    ]
```

### 前端頁面結構

```
┌─────────────────────────────────────┐
│  我的持股                 [共 N 筆]  │
├─────────────────────────────────────┤
│  2330.TW                            │
│  成本 850 | Hold | 信心 72.5        │  ← 最新一筆 daily_analysis_log
│  [查看歷史] [即時分析]              │
├─────────────────────────────────────┤
│  ▼ 展開歷史（呼叫 /history）        │
│  日期        建議   信心            │
│  2026-03-12  Hold   72.5            │
│  2026-03-11  Hold   61.5            │
└─────────────────────────────────────┘
```

- **最新一筆**：呼叫 `GET /portfolio/{id}/history?limit=1` 取得，顯示 `action_tag` 和 `signal_confidence`
- **「查看歷史」**：展開 inline 歷史清單（`limit=20`），精要顯示日期、建議、信心分數
- **「即時分析」**：點擊後導向 `/analyze` tab 並帶入該 symbol 預填，觸發分析（持股診斷模式，帶入 `entry_price`）

**驗收條件（MUST）**

- 後端存在 `GET /portfolio` 並可回傳 active 持倉列表。
- 前端 `我的持股` tab 預設渲染持倉列表，不再渲染 `PositionPage`。
- 每筆持倉可取得最新一筆診斷摘要（`limit=1`）與可展開歷史（`limit=20`）。
- 即時分析按鈕可帶入 symbol 並導向分析流程。

---

## Task 6：前端 — `PositionPage` 從「我的持股」tab 移出

**現況**：`activeTab === "position"` 渲染的是 `<PositionPage />`（輸入成本價做診斷），這個功能定位應改為「從持倉列表點進去的即時診斷」，而非獨立 tab。

**調整方向**：

- 把「我的持股」tab 換成 `<PortfolioPage />`
- `PositionPage` 的診斷表單邏輯，整合進 `PortfolioPage` 的「即時分析」按鈕流程，或保留為獨立的 modal/drawer

**驗收條件（MUST）**

- `activeTab === "position"` 不得再是「我的持股」入口頁。
- 使用者從「我的持股」入口只能看到持倉列表相關內容。

---

## 執行順序建議

```
[後端]
Task 1 → Task 2 → Task 3 → Task 5（新增 GET /portfolio）

[前端]
Task 4 → Task 5（PortfolioPage） → Task 6

[測試順序]
1. POST /analyze/position → 確認 stock_analysis_cache 有資料
2. 再次 POST 同 symbol → 確認 L1 快取命中（回應速度明顯提升）
3. POST /portfolio 加入持倉 → 確認 daily_analysis_log 有寫入
4. GET /portfolio/{id}/history → 確認歷史資料可讀取
5. 前端：分析後點「加入持股」Modal → 確認按鈕變「已追蹤」
6. 前端：切換我的持股頁 → 確認清單顯示、歷史展開、即時分析跳轉
```

---

## 注意事項

- **Task 2 的 L1 快取命中回傳格式**：`_handle_cache_hit` 回傳的是 `CachedAnalyzeResponse`，欄位比 `AnalyzeResponse` 少（只有精要欄位）。快取命中時前端仍需能渲染，建議快取命中後轉成 `AnalyzeResponse` 格式回傳，或讓前端能處理精簡版。最簡做法：快取命中時也回傳 `AnalyzeResponse`，把缺少的欄位留 null。
- **Task 2 的 L2 原始資料共用邏輯**：`stock_raw_data` 應作為兩支分析端點的共同資料來源；`stock_analysis_cache` 只保存分析結果與摘要快照，不應混放完整技術面 / 籌碼面 / 基本面 JSON。
- **`history_loader.py` 目前沒有被任何端點呼叫**，Task 3 修正後仍需在 Task 2 中實際接入 LangGraph 的 `initial_state`（這屬於 Phase 8 訊號轉向敘事的前置工作，本計劃不強制要求）。
- **`GET /portfolio` 是新端點**，執行前確認不需要 Alembic migration（Table 都已存在）。

---

## 快速驗收清單（DoD）

- [ ] Task 1：AnalyzeResponse 契約完成（雙端點）
- [ ] Task 2：雙端點接入快取 + 寫回策略一致
- [ ] Task 3：history_loader 查詢來源改為 stock_analysis_cache
- [ ] Task 4：/analyze 頁加入持股與盤中提示完整可用
- [ ] Task 5：我的持股頁與 GET /portfolio + history 串接完成
- [ ] Task 6：PositionPage 不再作為我的持股主入口
