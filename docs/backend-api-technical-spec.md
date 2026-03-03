# AI Stock Sentinel 後端 API 技術規格（v1）

> 類型：技術文件（Technical Doc）  
> 更新日期：2026-03-03

## 1) 目的

本文件定義目前後端 API 的實作契約與錯誤碼，供前後端串接、測試與除錯使用。

---

## 2) 服務啟動

```bash
cd backend
make run-api
```

預設位址：`http://127.0.0.1:8000`

---

## 3) Endpoint 契約

### `GET /health`

- **用途**：健康檢查
- **Response 200**

```json
{
  "status": "ok"
}
```

### `POST /analyze`

- **用途**：執行股票分析流程（crawler + analyzer + optional cleaner）

- **Request Body**

```json
{
  "symbol": "2330.TW",
  "news_text": "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%"
}
```

- **欄位說明**
  - `symbol`：股票代碼，必填，最小長度 1
  - `news_text`：新聞文字，選填

- **Response 200（成功/可降級成功）**

```json
{
  "snapshot": {
    "symbol": "2330.TW",
    "currency": "TWD",
    "current_price": 100.0,
    "previous_close": 99.0,
    "day_open": 99.5,
    "day_high": 101.0,
    "day_low": 98.5,
    "volume": 123456,
    "recent_closes": [98.0, 99.0, 100.0],
    "fetched_at": "2026-03-03T00:00:00+00:00"
  },
  "analysis": "...",
  "cleaned_news": {
    "date": "2026-03-03",
    "title": "台積電 2 月營收年增",
    "mentioned_numbers": ["2,600", "18.2%"],
    "sentiment_label": "positive"
  },
  "errors": []
}
```

---

## 4) 錯誤碼表（`errors[]`）

`errors` 為陣列，每筆格式如下：

```json
{
  "code": "ERROR_CODE",
  "message": "human readable message"
}
```

目前錯誤碼定義：

- `ANALYZE_RUNTIME_ERROR`：`agent.run()` 發生執行期例外
- `MISSING_SNAPSHOT`：agent 回傳缺少有效 `snapshot`
- `MISSING_ANALYSIS`：agent 回傳缺少有效 `analysis`

---

## 5) 驗證錯誤（422）

當 request body 不符合 schema（例如 `symbol` 為空字串），API 會回傳 `422 Unprocessable Entity`。

---

## 6) 測試對應

- 測試檔：`backend/tests/test_api.py`
- 覆蓋項目：
  - 健康檢查
  - 分析成功路徑
  - 請求驗證錯誤（422）
  - 執行期錯誤碼回傳
  - 缺欄位錯誤碼回傳
