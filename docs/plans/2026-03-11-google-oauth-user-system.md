# Google OAuth 使用者系統設計

> 類型：Phase 7 前置任務
> 日期：2026-03-10
> 狀態：Draft v1.0
> 定位：在 Phase 7 資料庫基礎設施之前，先建立使用者身份識別與認證機制

---

## 1. 系統定位與目標

### 1.1 背景

系統目前為無認證的 Anonymous API，所有請求皆無使用者身份。為了支援 Phase 7 的倉位記錄與歷史診斷 log，必須先建立使用者識別機制，才能將數據與使用者關聯。

### 1.2 使用情境

- **邀請制**：前端網址由管理員手動分享，不公開
- **系統層不做邀請驗證**：任何拿到網址的人皆可用 Google 帳號登入並自動建立帳號
- **資料模式**：個人持倉資料隔離（各自獨立），查詢歷史聚合用於模型優化

---

## 2. 認證架構

### 2.1 流程總覽

```
Frontend (React)
  │
  ├─ 使用者點擊「以 Google 登入」
  ├─ Google OAuth JS SDK → 取得 id_token
  ├─ POST /auth/google { id_token }
  │       └─ 後端驗證 id_token → 建立或查找 user → 簽發 JWT
  └─ 存 JWT 至 localStorage
       └─ 後續所有 API 請求帶 Authorization: Bearer <JWT>
```

### 2.2 設計決策

| 決策 | 選擇 | 理由 |
|------|------|------|
| OAuth 流程主導方 | 前端主導 | 前端用 Google JS SDK 取得 id_token，傳後端驗證，避免後端管理 redirect callback 的複雜度 |
| Session 管理 | JWT（無狀態） | 後端不需存 session，水平擴展友善 |
| 邀請控制方式 | 網址管控 | 系統不做額外驗證，由管理員控制前端網址的散佈 |

---

## 3. 資料庫 Schema

### 3.1 `users` 表

```sql
CREATE TABLE users (
    id          SERIAL PRIMARY KEY,
    google_sub  VARCHAR(255) NOT NULL UNIQUE,  -- Google id_token 的 sub 欄位，比 email 穩定
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(255),
    avatar_url  TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    deleted_at  TIMESTAMPTZ,                   -- 軟刪除，保留歷史 log 可追溯性
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_google_sub ON users (google_sub);
CREATE INDEX idx_users_email ON users (email);
```

**欄位說明**

| 欄位 | 說明 |
|------|------|
| `google_sub` | Google 的不可變使用者識別碼，即使使用者更改 email 也不會變 |
| `deleted_at` | 帳號刪除時設為當前時間而非實際刪除，確保 `daily_analysis_log` 歷史資料可保留 |
| `is_active` | 預留管理員停用帳號的能力 |

### 3.2 與 Phase 7 表的關聯設計

`user_portfolio` 和 `daily_analysis_log` 加入 `user_id` FK，但設為 **nullable**，原因：

1. 保留日後去識別化的彈性（使用者刪除帳號後，log 設 `user_id = NULL` 但資料保留供模型訓練）
2. Phase 7 若分析任務為系統自動觸發（非使用者主動查詢），`user_id` 可為 NULL

```sql
-- user_portfolio 新增欄位
ALTER TABLE user_portfolio
    ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- daily_analysis_log 新增欄位
ALTER TABLE daily_analysis_log
    ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX idx_portfolio_user_id ON user_portfolio (user_id);
CREATE INDEX idx_log_user_id ON daily_analysis_log (user_id);
```

**`ON DELETE SET NULL`**：使用者帳號刪除時，相關 log 的 `user_id` 自動設為 NULL，歷史數據保留供模型優化使用。

---

## 4. API 端點設計

### 4.1 認證端點

#### `POST /auth/google`

驗證 Google id_token，建立或查找使用者，回傳 JWT。

**Request**
```json
{
  "id_token": "<Google id_token>"
}
```

**Response**
```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "email": "user@gmail.com",
    "name": "User Name",
    "avatar_url": "https://..."
  }
}
```

**處理邏輯**
1. 呼叫 Google API 驗證 id_token（使用 `google-auth-library` 或直接呼叫 Google tokeninfo endpoint）
2. 從驗證結果取出 `sub`、`email`、`name`、`picture`
3. `SELECT * FROM users WHERE google_sub = ?`
   - 找到 → 更新 `name`、`avatar_url`（Google 資料可能更新）
   - 找不到 → `INSERT INTO users` 建立新帳號
4. 簽發 JWT（payload: `{ sub: user.id, email: user.email }`，exp: 7 天）

#### `GET /auth/me`

回傳當前登入使用者資訊，用於前端初始化。

**Response**
```json
{
  "id": 1,
  "email": "user@gmail.com",
  "name": "User Name",
  "avatar_url": "https://..."
}
```

### 4.2 受保護路由的認證方式

```python
# 所有需要認證的路由加上 Depends(get_current_user)
@app.post("/analyze/position", response_model=AnalyzeResponse)
async def analyze_position(
    payload: PositionAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    graph=Depends(get_graph),
) -> AnalyzeResponse:
    ...
```

`get_current_user` 從 `Authorization: Bearer <JWT>` 解析並驗證 JWT，回傳 User 物件。

---

## 5. 前端設計

### 5.1 登入頁面

- 路由：`/login`
- 未登入時所有路由重導至 `/login`
- 僅顯示「以 Google 帳號登入」按鈕

### 5.2 認證狀態管理

```typescript
// src/stores/auth.ts（或 Context）
interface AuthState {
  user: User | null
  token: string | null
  isLoading: boolean
}
```

- App 啟動時讀取 localStorage 的 JWT，呼叫 `GET /auth/me` 驗證有效性
- Token 過期或驗證失敗 → 清除 localStorage → 重導至 `/login`

### 5.3 API 請求攔截器

所有 API 請求自動帶上 JWT：

```typescript
// src/lib/api.ts
const headers = {
  'Authorization': `Bearer ${localStorage.getItem('token')}`,
  'Content-Type': 'application/json',
}
```

---

## 6. 後端實作結構

```
backend/src/ai_stock_sentinel/
├── auth/
│   ├── __init__.py
│   ├── router.py          # POST /auth/google, GET /auth/me
│   ├── jwt_handler.py     # JWT 簽發與驗證
│   ├── google_verifier.py # 驗證 Google id_token
│   └── dependencies.py    # get_current_user Depends
├── models/
│   └── user.py            # SQLAlchemy User ORM model
└── api.py                 # 掛載 auth router
```

---

## 7. 關鍵設計決策記錄

| 決策 | 選擇 | 理由 |
|------|------|------|
| 使用者識別碼 | `google_sub`（非 email） | email 可被使用者更改，`sub` 是 Google 的不可變 ID |
| 帳號刪除策略 | 軟刪除（`deleted_at`） | 歷史 log 去識別化後保留，供模型優化使用 |
| `user_id` FK 可空性 | Nullable | 保留去識別化彈性；系統觸發的分析任務不綁定特定使用者 |
| ON DELETE 行為 | `SET NULL` | 刪除使用者時 log 保留，`user_id` 設 NULL，不破壞歷史資料完整性 |
| JWT 有效期 | 7 天 | 邀請制小系統，便利性優先；若有安全需求可縮短並加 refresh token |

---

## 8. 實作任務清單

| 優先序 | 任務 | 說明 |
|--------|------|------|
| P0 | PostgreSQL 部署 + `users` 表 DDL | 認證系統的資料基礎 |
| P0 | `POST /auth/google` 端點 | 核心登入流程 |
| P0 | JWT 簽發與驗證（`jwt_handler.py`） | 所有認證的基礎 |
| P1 | `GET /auth/me` 端點 | 前端初始化使用者狀態 |
| P1 | `get_current_user` Depends | 保護現有 `/analyze/*` 路由 |
| P1 | 前端登入頁 + Google OAuth SDK 整合 | 使用者操作入口 |
| P1 | 前端認證狀態管理 + API 攔截器 | JWT 自動帶入所有請求 |
| P2 | `user_portfolio` / `daily_analysis_log` 加 `user_id` FK | Phase 7 資料關聯基礎 |

---

*文件版本：v1.0 | 最後更新：2026-03-10 | 下一步：PostgreSQL 部署 + users 表建立*
