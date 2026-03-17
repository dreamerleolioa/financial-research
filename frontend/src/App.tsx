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
              // brand color: no token defined
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
              `rounded-lg px-4 py-2 text-sm font-medium transition ${isActive
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
              `rounded-lg px-4 py-2 text-sm font-medium transition ${isActive
                ? "bg-indigo-600 text-white"
                : "border border-border bg-card text-text-muted hover:bg-card-hover"
              }`
            }
          >
            我的持股
          </NavLink>
          <NavLink
            to="/dashboard"
            className={({ isActive }) =>
              `rounded-lg px-4 py-2 text-sm font-medium transition ${isActive
                ? "bg-indigo-600 text-white"
                : "border border-border bg-card text-text-muted hover:bg-card-hover"
              }`
            }
          >
            復盤儀表板
          </NavLink>
        </nav>

        <Outlet />
      </div>
    </main>
  );
}
