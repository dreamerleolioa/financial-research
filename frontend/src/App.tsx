import { Outlet, useLocation } from "react-router-dom";
import {
  DesktopNavigation,
  MobileBottomNavigation,
  PortfolioSubNavigation,
} from "./components/app-shell/AppNavigation";
import { useAuth } from "./stores/auth";
import { useDarkMode } from "./stores/theme";
import { ProductBrand } from "./components/brand/ProductBrand";

const routeTitles = [
  { matches: (pathname: string) => pathname.startsWith("/active-etf"), title: "主動式 ETF 持股追蹤" },
  { matches: (pathname: string) => pathname.startsWith("/watchlist"), title: "關注列表" },
  { matches: (pathname: string) => pathname.startsWith("/portfolio"), title: "持股管理" },
  { matches: (pathname: string) => pathname.startsWith("/daily-radar"), title: "每日盤後觀察雷達" },
  { matches: () => true, title: "個股分析" },
];

function ThemeIcon({ theme }: { theme: "light" | "dark" }) {
  if (theme === "dark") {
    return (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        className="h-5 w-5"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="4" />
        <path
          d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-5 w-5" aria-hidden="true">
      <path d="M20.4 15.2A8.5 8.5 0 0 1 8.8 3.6 8.5 8.5 0 1 0 20.4 15.2Z" strokeLinejoin="round" />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-5 w-5" aria-hidden="true">
      <path
        d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M14 8l4 4-4 4m4-4H9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function UserAvatar({
  avatarUrl,
  name,
  size = "md",
}: {
  avatarUrl?: string | null;
  name?: string | null;
  size?: "sm" | "md";
}) {
  const sizeClass = size === "sm" ? "h-8 w-8 text-xs" : "h-9 w-9 text-sm";

  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt={name ? `${name} 的頭像` : "使用者頭像"}
        referrerPolicy="no-referrer"
        className={`${sizeClass} rounded-[10px] object-cover ring-1 ring-border`}
      />
    );
  }

  return (
    <span
      className={`flex ${sizeClass} items-center justify-center rounded-[10px] bg-accent-soft font-semibold text-accent`}
      aria-hidden="true"
    >
      {name ? name.charAt(0).toUpperCase() : "?"}
    </span>
  );
}

export default function App() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useDarkMode();
  const { pathname } = useLocation();
  const routeTitle = routeTitles.find((route) => route.matches(pathname))?.title ?? "個股分析";
  const isPortfolioRoute = pathname.startsWith("/portfolio");
  const themeLabel = theme === "dark" ? "切換為亮色模式" : "切換為暗色模式";

  return (
    <div className="min-h-dvh bg-canvas text-text-primary">
      <a
        href="#main-content"
        className="fixed top-3 left-3 z-50 -translate-y-20 rounded-[10px] bg-accent px-4 py-2 text-sm font-semibold text-accent-contrast shadow-panel transition-transform duration-150 focus:translate-y-0 motion-reduce:transition-none"
      >
        跳至主要內容
      </a>

      <div className="min-h-dvh lg:grid lg:grid-cols-[14rem_minmax(0,1fr)]">
        <aside className="sticky top-0 hidden h-dvh flex-col border-r border-border bg-shell px-4 py-5 lg:flex">
          <ProductBrand />
          <DesktopNavigation />

          <div className="border-t border-border pt-4">
            <div className="flex min-w-0 items-center gap-3 px-1">
              <UserAvatar avatarUrl={user?.avatar_url} name={user?.name} />
              <div className="min-w-0 flex-1 leading-tight">
                <p className="truncate text-sm font-medium text-text-primary">{user?.name ?? "使用者"}</p>
                {user?.email && <p className="mt-1 truncate text-xs text-text-faint">{user.email}</p>}
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={toggle}
                className="ui-button-secondary min-h-10 px-3 text-xs"
                aria-label={themeLabel}
                title={themeLabel}
              >
                <ThemeIcon theme={theme} />
                <span>{theme === "dark" ? "亮色" : "暗色"}</span>
              </button>
              <button type="button" onClick={logout} className="ui-button-secondary min-h-10 px-3 text-xs" title="登出">
                <LogoutIcon />
                <span>登出</span>
              </button>
            </div>
          </div>
        </aside>

        <div className="min-w-0">
          <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-shell/95 px-4 backdrop-blur-sm lg:hidden">
            <ProductBrand compact />
            <div className="flex shrink-0 items-center gap-1">
              <UserAvatar avatarUrl={user?.avatar_url} name={user?.name} size="sm" />
              <button
                type="button"
                onClick={toggle}
                className="ui-icon-button"
                aria-label={themeLabel}
                title={themeLabel}
              >
                <ThemeIcon theme={theme} />
              </button>
              <button type="button" onClick={logout} className="ui-icon-button" aria-label="登出" title="登出">
                <LogoutIcon />
              </button>
            </div>
          </header>

          <main
            id="main-content"
            tabIndex={-1}
            className="min-w-0 px-4 py-5 pb-28 sm:px-6 sm:py-6 lg:px-8 lg:py-8 lg:pb-10"
          >
            <div className="w-full max-w-[1440px]">
              <h1 className="sr-only">{routeTitle}</h1>
              {isPortfolioRoute && <PortfolioSubNavigation />}
              <Outlet />
            </div>
          </main>
        </div>
      </div>

      <MobileBottomNavigation />
    </div>
  );
}
