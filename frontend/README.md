# Frontend Dashboard

React + TypeScript + Tailwind 的前端儀表板專案。

## 套件管理

此專案使用 **pnpm**（非 npm）。

## 安裝與執行

```bash
pnpm install
pnpm dev
```

預設開啟：`http://localhost:5173`

## 常用指令

```bash
pnpm dev
pnpm build
pnpm preview
pnpm lint
pnpm test:e2e
```

## E2E 測試

E2E 使用 Playwright Chromium，測試會自行啟動隔離的 `http://127.0.0.1:4173`，不會使用開發中的 5173；若 4173 已被占用會直接失敗，避免誤測其他服務。

第一次執行先安裝瀏覽器：

```bash
pnpm test:e2e:install
pnpm test:e2e
```

測試以 Playwright route interception 提供固定的登入使用者與 API fixture，不需要真實 Google 帳號、個人瀏覽器 session、production data 或啟動 backend。登入狀態只在測試 browser context 寫入假 token。

若本機已安裝 Chromium-based browser，也可指定執行檔：

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" pnpm test:e2e
```

主要保護範圍：

- Login、OAuth callback 與 protected route
- Desktop sidebar、mobile bottom navigation、portfolio 子檢視與 theme persistence
- Analyze、Watchlist、Portfolio、Closed Portfolio、Daily Radar 的核心流程
- Copy-to-AI、刪除確認、dialog/drawer keyboard focus
- 1280px、375px、320px 的核心 route 水平溢出

## 目前頁面內容

目前頁面包含：

- `/analyze`：新倉分析與加入持股
- `/portfolio`：未結案持股列表、診斷歷史、編輯與出場結案
- `/portfolio/closed`：已結案持股紀錄與已實現損益篩選
- `/daily-radar`：每日觀察候選清單
- `/login`：Google OAuth 登入

安裝、開發、build、preview 與 lint 仍使用上方 pnpm 指令。
