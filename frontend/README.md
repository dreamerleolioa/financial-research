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
```

## 目前頁面內容

目前頁面包含：

- `/analyze`：新倉分析與加入持股
- `/portfolio`：未結案持股列表、診斷歷史、編輯與出場結案
- `/portfolio/closed`：已結案持股紀錄與已實現損益篩選
- `/daily-radar`：每日觀察候選清單
- `/login`：Google OAuth 登入

安裝、開發、build、preview 與 lint 仍使用上方 pnpm 指令。
