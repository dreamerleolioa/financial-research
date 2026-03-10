# CI/CD: GitHub Pages + Render 部署計劃

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 設定 GitHub Actions CI/CD，前端自動部署到 GitHub Pages，後端自動部署到 Render。

**Architecture:** Push to `main` → GitHub Actions 跑測試 → 前端 build 後 deploy 到 GitHub Pages → 透過 Render Deploy Hook 觸發後端重新部署。前端透過環境變數指定後端 API URL，兩者獨立部署互不影響。

**Tech Stack:** GitHub Actions, GitHub Pages, Render (free tier), Vite (`base` config for Pages), FastAPI + Uvicorn

---

## 前置作業（手動，需在 GitHub/Render 操作）

這些步驟需要你手動完成，才能讓 CI/CD 正常運作。

### A. 建立 Render 帳號與後端服務

1. 前往 [render.com](https://render.com) 註冊帳號（可用 GitHub 登入）
2. 點 **New → Web Service**
3. 連接你的 GitHub repo
4. 設定：
   - **Name**: `ai-stock-sentinel-api`（或任意名稱）
   - **Branch**: `main`
   - **Root Directory**: `backend`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn src.ai_stock_sentinel.api:app --host 0.0.0.0 --port $PORT`
5. 在 **Environment Variables** 加入：
   - `ANTHROPIC_API_KEY` = 你的 API key
   - `ANTHROPIC_MODEL` = `claude-sonnet-4-5`
6. 部署後，複製你的服務 URL，格式為 `https://ai-stock-sentinel-api.onrender.com`

### B. 取得 Render Deploy Hook URL

1. 進入你的 Render 服務頁面
2. 點 **Settings → Deploy Hook**
3. 複製該 URL，格式為 `https://api.render.com/deploy/srv-xxxx?key=yyyy`

### C. 設定 GitHub Secrets

在你的 GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**，新增：

| Secret 名稱 | 值 |
|-------------|-----|
| `RENDER_DEPLOY_HOOK_URL` | 步驟 B 取得的 Deploy Hook URL |

### D. 啟用 GitHub Pages

1. GitHub repo → **Settings → Pages**
2. **Source**: 選 `GitHub Actions`（不是 branch）

---

## Task 1: 設定 Vite base URL for GitHub Pages

**Files:**
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/package.json`（新增 env 相關 script，非必要）

GitHub Pages 的部署路徑是 `https://<username>.github.io/<repo-name>/`，Vite 預設 `base: '/'` 會導致靜態資源 404。需要透過環境變數動態設定。

**Step 1: 修改 vite.config.ts**

將 [frontend/vite.config.ts](frontend/vite.config.ts) 改為：

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.VITE_BASE_URL || '/',
  define: {
    'import.meta.env.VITE_API_BASE_URL': JSON.stringify(process.env.VITE_API_BASE_URL || 'http://localhost:8000'),
  },
})
```

**Step 2: 確認本機 build 正常**

```bash
cd frontend
pnpm build
```

Expected: `dist/` 目錄產生，無 TypeScript 錯誤

**Step 3: Commit**

```bash
git add frontend/vite.config.ts
git commit -m "feat: add base URL and API URL env config for Vite"
```

---

## Task 2: 更新前端 API URL 為環境變數

**Files:**
- Modify: `frontend/src/App.tsx`（將 hardcode 的 localhost:8000 改為 env var）

**Step 1: 確認目前 App.tsx 裡 API URL 的寫法**

在 [frontend/src/App.tsx](frontend/src/App.tsx) 找到 `localhost:8000` 或 `fetch(` 的位置。

**Step 2: 將 API base URL 改為讀取環境變數**

找到類似這樣的程式碼：
```typescript
const response = await fetch('http://localhost:8000/analyze', { ... })
```

改為：
```typescript
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const response = await fetch(`${API_BASE}/analyze`, { ... })
```

**Step 3: 確認本機開發正常**

```bash
cd frontend
pnpm dev
```

瀏覽 `http://localhost:5173`，確認功能正常

**Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: use VITE_API_BASE_URL env var for API endpoint"
```

---

## Task 3: 更新後端 CORS 允許 GitHub Pages URL

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`

目前 CORS 只允許 `localhost`，部署後前端的 origin 會是 `https://<username>.github.io`，需要加入。

**Step 1: 修改 api.py 的 CORS 設定**

找到 [backend/src/ai_stock_sentinel/api.py:57-62](backend/src/ai_stock_sentinel/api.py#L57-L62)：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

改為讀取環境變數（讓 CORS origin 可動態設定）：

```python
import os

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174")
_allowed_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Step 2: 在 Render 環境變數加入 CORS_ORIGINS**（手動操作）

等 workflow 完成後，在 Render 的 Environment Variables 加入：
```
CORS_ORIGINS=http://localhost:5173,https://<your-username>.github.io
```

> 注意：`<your-username>` 換成你的 GitHub 帳號名稱

**Step 3: 跑後端測試確認沒有破壞任何東西**

```bash
cd backend
pytest tests/ -v
```

Expected: 所有測試 PASS

**Step 4: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py
git commit -m "feat: make CORS origins configurable via env var"
```

---

## Task 4: 建立 GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

**Step 1: 建立目錄**

```bash
mkdir -p .github/workflows
```

**Step 2: 建立 workflow 檔案**

建立 `.github/workflows/deploy.yml`，內容如下：

```yaml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  # ── 後端測試 ─────────────────────────────────────────────────
  test-backend:
    name: Backend Tests
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
          cache-dependency-path: backend/requirements.txt

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run tests
        run: pytest tests/ -v
        env:
          # 測試時用假 key，避免真實呼叫 API
          ANTHROPIC_API_KEY: test-key

  # ── 前端 Build + Deploy to GitHub Pages ──────────────────────
  deploy-frontend:
    name: Deploy Frontend to GitHub Pages
    runs-on: ubuntu-latest
    needs: test-backend
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'

    permissions:
      contents: read
      pages: write
      id-token: write

    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}

    defaults:
      run:
        working-directory: frontend

    steps:
      - uses: actions/checkout@v4

      - name: Setup pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 10

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: "pnpm"
          cache-dependency-path: frontend/pnpm-lock.yaml

      - name: Install dependencies
        run: pnpm install --frozen-lockfile

      - name: Build
        run: pnpm build
        env:
          # GitHub Pages 的 base path，格式 /<repo-name>/
          VITE_BASE_URL: /${{ github.event.repository.name }}/
          # Render 後端 URL（需在 GitHub Actions Variables 設定）
          VITE_API_BASE_URL: ${{ vars.VITE_API_BASE_URL }}

      - name: Setup Pages
        uses: actions/configure-pages@v5

      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: frontend/dist

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4

  # ── 觸發 Render 後端重新部署 ──────────────────────────────────
  deploy-backend:
    name: Deploy Backend to Render
    runs-on: ubuntu-latest
    needs: test-backend
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'

    steps:
      - name: Trigger Render Deploy Hook
        run: |
          curl -s -X POST "${{ secrets.RENDER_DEPLOY_HOOK_URL }}" \
            --fail \
            --output /dev/null
          echo "Render deploy triggered successfully"
```

**Step 3: 在 GitHub 設定 VITE_API_BASE_URL Variable**（手動操作）

這不是 Secret（不需要保密），所以用 **Variables** 而非 Secrets：

GitHub repo → **Settings → Secrets and variables → Actions → Variables → New repository variable**

| Variable 名稱 | 值 |
|---------------|-----|
| `VITE_API_BASE_URL` | `https://ai-stock-sentinel-api.onrender.com`（你的 Render URL）|

**Step 4: Commit & Push**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat: add GitHub Actions CI/CD for GitHub Pages and Render"
git push origin main
```

**Step 5: 確認 Actions 跑起來**

前往 GitHub repo → **Actions** tab，確認 workflow 開始執行。

---

## Task 5: 驗證部署結果

**Step 1: 確認後端 Render 部署**

- Render dashboard 應顯示 deploy triggered / in progress
- 部署完成後，curl 測試 health endpoint：
  ```bash
  curl https://ai-stock-sentinel-api.onrender.com/health
  ```
  Expected: `{"status":"ok"}`

**Step 2: 確認前端 GitHub Pages**

- GitHub Actions 完成後，前往：
  `https://<your-username>.github.io/<repo-name>/`
- 確認頁面正常載入

**Step 3: 確認前後端連線**

在 GitHub Pages 的前端頁面，執行一次股票分析，確認能成功呼叫 Render 後端。

> 注意：Render 免費方案閒置 15 分鐘後會 sleep，第一次呼叫需等 ~30 秒喚醒。

---

## 總結：部署架構

```
push to main
    │
    ├── test-backend (pytest)
    │       │
    │       ├── PASS → deploy-frontend (Vite build → GitHub Pages)
    │       └── PASS → deploy-backend (curl Render Deploy Hook)
    │
    └── FAIL → 停止，不部署
```

**各服務 URL：**
- 前端：`https://<username>.github.io/<repo-name>/`
- 後端：`https://<service-name>.onrender.com`

**需要設定的 GitHub Secrets/Variables：**
| 類型 | 名稱 | 說明 |
|------|------|------|
| Secret | `RENDER_DEPLOY_HOOK_URL` | Render Deploy Hook URL |
| Variable | `VITE_API_BASE_URL` | Render 後端 URL |

**需要設定的 Render Environment Variables：**
| 名稱 | 值 |
|------|-----|
| `ANTHROPIC_API_KEY` | 你的 Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5` |
| `CORS_ORIGINS` | `http://localhost:5173,https://<username>.github.io` |
