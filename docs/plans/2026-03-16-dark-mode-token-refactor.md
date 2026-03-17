# Dark Mode Token Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 將 dark mode 從「每個 class 都加 `dark:` prefix」重構為 Tailwind v4 `@theme` semantic token 做法，讓元件寫法更語意化、不需重複寫雙份色彩 class。

**Architecture:** 在 `index.css` 用 Tailwind v4 的 `@theme` 定義 semantic color token（如 `--color-surface`、`--color-text-primary` 等），並透過 `@variant dark` 在 `.dark` selector 下覆蓋 token 值，元件改用語意化 class（`bg-surface`、`text-primary` 等），完全移除 `dark:` prefix。

**Tech Stack:** Tailwind CSS v4（`@import 'tailwindcss'`、`@theme`、`@variant dark`）、React、TypeScript

---

## Token 設計（參考表）

| Token | Light | Dark | 用途 |
|---|---|---|---|
| `--color-surface` | `slate-50` | `slate-900` | 頁面背景 |
| `--color-card` | `white` | `slate-800` | card/panel 背景 |
| `--color-card-hover` | `slate-50` | `slate-700` | card hover 背景 |
| `--color-border` | `slate-200` | `slate-700` | 通用邊框 |
| `--color-border-subtle` | `slate-100` | `slate-700` | 較淡分隔線 |
| `--color-text-primary` | `slate-900` | `slate-100` | 主文字 |
| `--color-text-secondary` | `slate-700` | `slate-300` | 次要文字 |
| `--color-text-muted` | `slate-500` | `slate-400` | 弱文字（label、hint）|
| `--color-text-faint` | `slate-400` | `slate-500` | 最弱文字（placeholder）|
| `--color-input-bg` | `white` | `slate-700` | input 背景 |
| `--color-input-border` | `slate-300` | `slate-600` | input 邊框 |
| `--color-badge-neutral-bg` | `slate-100` | `slate-700` | 中性 badge 背景 |
| `--color-badge-neutral-text` | `slate-700` | `slate-400` | 中性 badge 文字 |

> **注意：** `bg-indigo-600`、`bg-emerald-100 text-emerald-800` 這類固定語意色（品牌色、信號色）不需要 token 化，直接保留原始 class。

---

### Task 1: 在 index.css 定義 @theme tokens

**Files:**
- Modify: `frontend/src/index.css`

**Step 1: 新增 `@theme` 與 dark override 到 index.css**

將 `frontend/src/index.css` 改為：

```css
@import 'tailwindcss';

@variant dark (&:where(.dark, .dark *));

@theme {
  --color-surface: theme(colors.slate.50);
  --color-card: theme(colors.white);
  --color-card-hover: theme(colors.slate.50);
  --color-border: theme(colors.slate.200);
  --color-border-subtle: theme(colors.slate.100);
  --color-text-primary: theme(colors.slate.900);
  --color-text-secondary: theme(colors.slate.700);
  --color-text-muted: theme(colors.slate.500);
  --color-text-faint: theme(colors.slate.400);
  --color-input-bg: theme(colors.white);
  --color-input-border: theme(colors.slate.300);
  --color-badge-neutral-bg: theme(colors.slate.100);
  --color-badge-neutral-text: theme(colors.slate.700);
}

.dark {
  --color-surface: theme(colors.slate.900);
  --color-card: theme(colors.slate.800);
  --color-card-hover: theme(colors.slate.700);
  --color-border: theme(colors.slate.700);
  --color-border-subtle: theme(colors.slate.700);
  --color-text-primary: theme(colors.slate.100);
  --color-text-secondary: theme(colors.slate.300);
  --color-text-muted: theme(colors.slate.400);
  --color-text-faint: theme(colors.slate.500);
  --color-input-bg: theme(colors.slate.700);
  --color-input-border: theme(colors.slate.600);
  --color-badge-neutral-bg: theme(colors.slate.700);
  --color-badge-neutral-text: theme(colors.slate.400);
}

body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
}
```

**Step 2: 啟動開發伺服器確認 CSS 可正常編譯（無 build error）**

```bash
cd frontend && pnpm dev
```

Expected: 無報錯，瀏覽器可正常顯示頁面

**Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat: define semantic color tokens via Tailwind v4 @theme"
```

---

### Task 2: 重構 App.tsx

**Files:**
- Modify: `frontend/src/App.tsx`

**Step 1: 替換 App.tsx 中所有 dark mode class**

對照表：
- `bg-slate-50 dark:bg-slate-900` → `bg-surface`
- `text-slate-900 dark:text-slate-100` → `text-text-primary`
- `text-slate-600 dark:text-slate-400` → `text-text-muted`
- `text-slate-800 dark:text-slate-100` → `text-text-primary`
- `border-slate-200 dark:border-slate-700` → `border-border`
- `bg-white dark:bg-slate-800` → `bg-card`
- `bg-slate-200 dark:bg-slate-600`（分隔線）→ `bg-border`
- `hover:bg-slate-100 dark:hover:bg-slate-700` → `hover:bg-card-hover`
- `text-slate-500 dark:text-slate-400` → `text-text-muted`
- `dark:text-slate-200` + `text-slate-800` → `text-text-primary`
- `dark:text-slate-500` + `text-xs text-slate-400` → `text-text-faint`
- `dark:ring-indigo-900` + `ring-indigo-100` → 保留（品牌色，不 token 化）
- `dark:border-slate-700 dark:bg-slate-700` 的 NavLink inactive → `border-border bg-card text-text-muted hover:bg-card-hover`

完整替換後的 App.tsx：

```tsx
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "./stores/auth";
import { useDarkMode } from "./stores/theme";

export default function App() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useDarkMode();

  return (
    <main className="min-h-screen bg-surface text-text-primary">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 md:px-6">
        <header className="flex items-start justify-between gap-2">
          <div>
            <h1 className="text-2xl font-semibold md:text-3xl">個股分析儀表板</h1>
            <p className="text-sm text-text-muted">
              輸入股票代碼，查看 AI 分析信心、雜訊過濾結果與流程路徑。
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 shadow-sm">
            {user?.avatar_url ? (
              <img
                src={user.avatar_url}
                alt={user.name ?? "使用者"}
                referrerPolicy="no-referrer"
                className="h-8 w-8 rounded-full object-cover ring-2 ring-indigo-100 dark:ring-indigo-900"
              />
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-600 dark:bg-indigo-900 dark:text-indigo-300">
                {user?.name ? user.name.charAt(0).toUpperCase() : "?"}
              </div>
            )}
            {user?.name && (
              <div className="flex flex-col leading-tight">
                <span className="text-sm font-medium text-text-primary">{user.name}</span>
                {user?.email && <span className="text-xs text-text-faint">{user.email}</span>}
              </div>
            )}
            <div className="mx-1 h-5 w-px bg-border" />
            <button
              onClick={toggle}
              className="rounded-lg p-1.5 text-text-muted transition hover:bg-card-hover hover:text-text-secondary"
              title={theme === "dark" ? "切換為亮色模式" : "切換為暗色模式"}
            >
              {theme === "dark" ? (
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
                </svg>
              ) : (
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z" />
                </svg>
              )}
            </button>
            <div className="mx-1 h-5 w-px bg-border" />
            <button
              onClick={logout}
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-muted transition hover:bg-card-hover hover:text-text-secondary"
              title="登出"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              登出
            </button>
          </div>
        </header>

        <nav className="flex gap-2">
          <NavLink
            to="/analyze"
            className={({ isActive }) =>
              `rounded-lg px-4 py-2 text-sm font-medium transition ${
                isActive
                  ? "bg-indigo-600 text-white"
                  : "border border-border bg-card text-text-muted hover:bg-card-hover"
              }`
            }
          >
            個股分析
          </NavLink>
          <NavLink
            to="/portfolio"
            className={({ isActive }) =>
              `rounded-lg px-4 py-2 text-sm font-medium transition ${
                isActive
                  ? "bg-indigo-600 text-white"
                  : "border border-border bg-card text-text-muted hover:bg-card-hover"
              }`
            }
          >
            我的持股
          </NavLink>
        </nav>

        <Outlet />
      </div>
    </main>
  );
}
```

**Step 2: 確認頁面 light/dark 切換正常**

在瀏覽器手動切換 dark mode，確認 header、nav 顏色正確切換。

**Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "refactor: use semantic color tokens in App.tsx"
```

---

### Task 3: 重構 AnalyzePage.tsx（通用元素）

**Files:**
- Modify: `frontend/src/pages/AnalyzePage.tsx`

**重構對照（AnalyzePage 共用模式）：**

| 舊寫法 | 新寫法 |
|---|---|
| `border-slate-200 bg-white ... dark:border-slate-700 dark:bg-slate-800` | `border-border bg-card` |
| `text-slate-700 dark:text-slate-300` | `text-text-secondary` |
| `text-slate-800 dark:text-slate-100` | `text-text-primary` |
| `text-slate-500 dark:text-slate-400` | `text-text-muted` |
| `text-slate-400 dark:text-slate-500` | `text-text-faint` |
| `dark:text-slate-300` in `InsightText` | `text-text-secondary` |
| `dark:stroke-slate-700` + `stroke-slate-200`（circle）| `stroke-border` |
| `bg-slate-100 dark:bg-slate-700`（badge 中性）| `bg-badge-neutral-bg` |
| `text-slate-400 dark:text-slate-700`（badge text）| `text-badge-neutral-text` |
| `divide-slate-100 dark:divide-slate-700` | `divide-border-subtle` |
| `dark:bg-slate-700` + `bg-slate-50`（投資策略 dl item）| `bg-card-hover` |
| `border-slate-300 dark:border-slate-600` (input) | `border-input-border` |
| `bg-white dark:bg-slate-700` (input) | `bg-input-bg` |
| `text-slate-900 dark:text-slate-100` (input) | `text-text-primary` |
| `hover:text-indigo-600 dark:hover:text-indigo-400`（新聞連結）| 保留（品牌色）|
| `bg-slate-100 dark:bg-slate-700` (品質受限提示) | `bg-badge-neutral-bg text-text-muted` |
| `dark:bg-slate-800 dark:border-slate-700`（modal card）| `bg-card border-border` |
| `text-slate-600 dark:text-slate-400`（label）| `text-text-muted` |
| `text-slate-500 dark:text-slate-400`（readonly input）| `text-text-muted` |
| `bg-slate-50 dark:bg-slate-700`（readonly input bg）| `bg-card-hover` |
| `border-indigo-100 bg-indigo-50 dark:border-indigo-900 dark:bg-indigo-950`（綜合仲裁）| 保留（品牌色）|
| `border-indigo-100 dark:border-indigo-900`（風險提示分隔）| 保留（品牌色）|

**Step 1: 全域替換 AnalyzePage.tsx**

逐一替換上表的 class，`InsightText` 元件改為：

```tsx
function InsightText({ text }: { text: string | null | undefined }) {
  if (!text) return <p className="text-sm text-text-faint">請先執行分析。</p>;
  const sentences = text.split(/(?<=[。；！？：\n])/).map((s) => s.trim()).filter(Boolean);
  if (sentences.length <= 1)
    return <p className="text-sm leading-relaxed text-text-secondary">{text}</p>;
  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => (
        <p key={i} className="text-sm leading-relaxed text-text-secondary">{s}</p>
      ))}
    </div>
  );
}
```

`SIGNAL_CLASS`、`SENTIMENT_CLASS`、`PE_BAND_BADGE`、`INST_FLOW_BADGE` 中的中性項目改用 token：

```tsx
const SIGNAL_CLASS: Record<string, string> = {
  bullish: "bg-emerald-100 text-emerald-800",
  bearish: "bg-red-100 text-red-800",
  sideways: "bg-badge-neutral-bg text-badge-neutral-text",
};

const SENTIMENT_CLASS: Record<string, string> = {
  positive: "bg-emerald-100 text-emerald-800",
  neutral: "bg-badge-neutral-bg text-badge-neutral-text",
  negative: "bg-rose-100 text-rose-800",
};

const PE_BAND_BADGE: Record<string, { label: string; cls: string }> = {
  cheap: { label: "低估", cls: "bg-emerald-100 text-emerald-800" },
  fair: { label: "合理", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
  expensive: { label: "高估", cls: "bg-red-100 text-red-800" },
};

const INST_FLOW_BADGE: Record<string, { label: string; cls: string }> = {
  institutional_accumulation: { label: "法人買超", cls: "bg-emerald-100 text-emerald-800" },
  distribution: { label: "主力出貨", cls: "bg-red-100 text-red-800" },
  retail_chasing: { label: "散戶追高", cls: "bg-orange-100 text-orange-800" },
  neutral: { label: "籌碼中性", cls: "bg-badge-neutral-bg text-badge-neutral-text" },
};
```

空 badge placeholder（`<span className="inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-400 dark:bg-slate-700">`）改為：

```tsx
<span className="inline-block rounded-full bg-badge-neutral-bg px-2 py-0.5 text-xs font-semibold text-badge-neutral-text">—</span>
```

**Step 2: 確認 light/dark 切換，AnalyzePage 所有 section 顏色正確**

**Step 3: Commit**

```bash
git add frontend/src/pages/AnalyzePage.tsx
git commit -m "refactor: use semantic color tokens in AnalyzePage.tsx"
```

---

### Task 4: 重構 PortfolioPage.tsx

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx`

**重構對照（PortfolioPage）：**

| 舊寫法 | 新寫法 |
|---|---|
| `border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800` | `border-border bg-card` |
| `text-slate-800 dark:text-slate-100` | `text-text-primary` |
| `text-slate-600 dark:text-slate-400` | `text-text-muted` |
| `text-slate-500 dark:text-slate-400` | `text-text-muted` |
| `text-slate-400 dark:text-slate-500` | `text-text-faint` |
| `text-slate-700 dark:text-slate-300`（InsightText）| `text-text-secondary` |
| `border-slate-100 dark:border-slate-700`（border-b/t）| `border-border-subtle` |
| `hover:bg-slate-100 dark:hover:bg-slate-700` | `hover:bg-card-hover` |
| `hover:text-slate-600 dark:hover:text-slate-300` | `hover:text-text-secondary` |
| `rounded-md bg-slate-100 dark:bg-slate-700` (badge) | `bg-badge-neutral-bg text-badge-neutral-text` |
| `divide-slate-50 dark:divide-slate-700`（table）| `divide-border-subtle` |
| `text-slate-700 dark:text-slate-300`（table row）| `text-text-secondary` |
| `border-slate-300 dark:border-slate-600`（input）| `border-input-border` |
| `bg-white dark:bg-slate-700`（input）| `bg-input-bg` |
| `text-slate-900 dark:text-slate-100`（input）| `text-text-primary` |
| `border-slate-200 dark:border-slate-600`（cancel btn）| `border-border` |
| `text-slate-600 dark:text-slate-400`（cancel btn）| `text-text-muted` |
| `dark:bg-slate-700`（close btn hover）| `hover:bg-card-hover` |
| `dark:hover:text-slate-300`（close btn hover）| `hover:text-text-secondary` |
| `border-indigo-100 bg-indigo-50 dark:border-indigo-900 dark:bg-indigo-950` | 保留（品牌色）|
| `bg-red-950`、`bg-amber-950` 等信號色 | 保留（語意信號色）|

`InsightText`（PortfolioPage 版本）改為：

```tsx
function InsightText({ text }: { text: string | null | undefined }) {
  if (!text) return <p className="text-sm text-text-faint">—</p>;
  const sentences = text
    .split(/(?<=[。；！？：\n])/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (sentences.length <= 1)
    return <p className="text-sm leading-relaxed text-text-secondary">{text}</p>;
  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => (
        <p key={i} className="text-sm leading-relaxed text-text-secondary">
          {s}
        </p>
      ))}
    </div>
  );
}
```

**Step 1: 逐一替換 PortfolioPage.tsx 中所有符合上表的 class**

**Step 2: 確認 light/dark 切換，PortfolioPage 所有元素顏色正確**

**Step 3: Commit**

```bash
git add frontend/src/pages/PortfolioPage.tsx
git commit -m "refactor: use semantic color tokens in PortfolioPage.tsx"
```

---

### Task 5: 驗收

**Step 1: 全域搜尋確認沒有遺漏的 `dark:` 關鍵字（品牌色除外）**

```bash
cd frontend && grep -rn "dark:" src/App.tsx src/pages/AnalyzePage.tsx src/pages/PortfolioPage.tsx
```

允許殘留的 `dark:` 只有：
- `dark:ring-indigo-900`（avatar ring）
- `dark:bg-indigo-900 dark:text-indigo-300`（avatar fallback）
- `dark:border-indigo-900 dark:bg-indigo-950`（綜合仲裁 card）
- `dark:border-indigo-900`（風險提示分隔）
- `dark:text-indigo-400`（綜合仲裁標題、新聞連結 hover）
- `dark:border-red-900 dark:bg-red-950 dark:text-red-400`（error/delete 信號）
- `dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300`（amber 提示）
- `dark:bg-red-950 dark:text-red-400`（PortfolioPage 錯誤 banner）
- `dark:text-orange-400`（防守位）

其餘 `dark:` 都不應出現。

**Step 2: 在瀏覽器完整驗收**

- Light mode：頁面整體呈現正確的淺色
- Dark mode：切換後所有 section 正確呈現深色
- 品牌色（indigo、emerald、red、orange）在兩個 mode 下表現不變

**Step 3: Commit（若有收尾修改）**

```bash
git add -p
git commit -m "fix: dark mode token refactor cleanup"
```
