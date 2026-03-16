import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "./stores/auth";

export default function App() {
  const { user, logout } = useAuth();

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 md:px-6">
        <header className="flex items-start justify-between gap-2">
          <div>
            <h1 className="text-2xl font-semibold md:text-3xl">個股分析儀表板</h1>
            <p className="text-sm text-slate-600">
              輸入股票代碼，查看 AI 分析信心、雜訊過濾結果與流程路徑。
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
            {user?.avatar_url ? (
              <img
                src={user.avatar_url}
                alt={user.name ?? "使用者"}
                referrerPolicy="no-referrer"
                className="h-8 w-8 rounded-full object-cover ring-2 ring-indigo-100"
              />
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-600">
                {user?.name ? user.name.charAt(0).toUpperCase() : "?"}
              </div>
            )}
            {user?.name && (
              <div className="flex flex-col leading-tight">
                <span className="text-sm font-medium text-slate-800">{user.name}</span>
                {user?.email && <span className="text-xs text-slate-400">{user.email}</span>}
              </div>
            )}
            <div className="mx-1 h-5 w-px bg-slate-200" />
            <button
              onClick={logout}
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
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
                  : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
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
                  : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
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
