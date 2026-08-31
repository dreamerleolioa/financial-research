import type { ComponentType, SVGProps } from "react";
import { NavLink, useLocation } from "react-router-dom";

type IconProps = SVGProps<SVGSVGElement>;

type NavigationItem = {
  label: string;
  mobileLabel: string;
  to: string;
  icon: ComponentType<IconProps>;
  matches: (pathname: string) => boolean;
};

function AnalysisIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      {...props}
    >
      <path d="M4 19V9m5 10V5m6 14v-7m5 7V3" strokeLinecap="round" />
      <path d="M3 21h18" strokeLinecap="round" />
    </svg>
  );
}

function WatchlistIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      {...props}
    >
      <path d="M7 4h10a2 2 0 0 1 2 2v14l-7-4-7 4V6a2 2 0 0 1 2-2Z" strokeLinejoin="round" />
      <path d="M9 9h6M9 12h4" strokeLinecap="round" />
    </svg>
  );
}

function PortfolioIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      {...props}
    >
      <path d="M4 8h16v11H4z" strokeLinejoin="round" />
      <path d="M8 8V5h8v3M4 12h16" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10 12v2h4v-2" strokeLinejoin="round" />
    </svg>
  );
}

function RadarIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      {...props}
    >
      <circle cx="12" cy="12" r="8" />
      <path d="m12 12 5-5M12 4v2M4 12h2M12 18v2M18 12h2" strokeLinecap="round" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  );
}

function EtfIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
      {...props}
    >
      <path d="M5 5h14v14H5z" strokeLinejoin="round" />
      <path d="M8 15.5 11 12l2.5 2 3-5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 8h3" strokeLinecap="round" />
    </svg>
  );
}

const navigationItems: NavigationItem[] = [
  {
    label: "個股分析",
    mobileLabel: "分析",
    to: "/analyze",
    icon: AnalysisIcon,
    matches: (pathname) => pathname.startsWith("/analyze"),
  },
  {
    label: "關注列表",
    mobileLabel: "關注",
    to: "/watchlist",
    icon: WatchlistIcon,
    matches: (pathname) => pathname.startsWith("/watchlist"),
  },
  {
    label: "持股管理",
    mobileLabel: "持股",
    to: "/portfolio",
    icon: PortfolioIcon,
    matches: (pathname) => pathname.startsWith("/portfolio"),
  },
  {
    label: "盤後觀察雷達",
    mobileLabel: "雷達",
    to: "/daily-radar",
    icon: RadarIcon,
    matches: (pathname) => pathname.startsWith("/daily-radar"),
  },
  {
    label: "主動式 ETF",
    mobileLabel: "ETF",
    to: "/active-etf",
    icon: EtfIcon,
    matches: (pathname) => pathname.startsWith("/active-etf"),
  },
];

export function DesktopNavigation() {
  const { pathname } = useLocation();

  return (
    <nav aria-label="主要功能" className="mt-8 flex-1">
      <p className="px-3 text-[0.6875rem] font-semibold tracking-[0.14em] text-text-faint uppercase">
        研究工作區
      </p>
      <ul className="mt-3 space-y-1">
        {navigationItems.map((item) => {
          const isCurrent = item.matches(pathname);
          const Icon = item.icon;

          return (
            <li key={item.to}>
              <NavLink
                to={item.to}
                aria-current={isCurrent ? "page" : undefined}
                className={`group relative flex min-h-11 items-center gap-3 rounded-[10px] px-3 text-sm font-medium transition-colors duration-150 active:scale-[0.98] motion-reduce:transform-none ${
                  isCurrent
                    ? "bg-accent-soft text-text-primary"
                    : "text-text-muted hover:bg-card-hover hover:text-text-primary"
                }`}
              >
                <span
                  className={`absolute inset-y-2 left-0 w-0.5 rounded-full transition-opacity duration-150 ${
                    isCurrent ? "bg-accent opacity-100" : "opacity-0"
                  }`}
                  aria-hidden="true"
                />
                <Icon
                  className={`h-5 w-5 ${isCurrent ? "text-accent" : "text-text-faint group-hover:text-text-muted"}`}
                />
                <span>{item.label}</span>
              </NavLink>

              {item.to === "/portfolio" && isCurrent && (
                <div className="mt-1 ml-11 grid grid-cols-2 gap-1" aria-label="持股檢視">
                  <NavLink
                    to="/portfolio"
                    end
                    className={({ isActive }) =>
                      `rounded-md px-2 py-1.5 text-center text-xs transition-colors duration-150 ${
                        isActive
                          ? "bg-surface-raised font-medium text-accent"
                          : "text-text-faint hover:text-text-muted"
                      }`
                    }
                  >
                    持有中
                  </NavLink>
                  <NavLink
                    to="/portfolio/closed"
                    className={({ isActive }) =>
                      `rounded-md px-2 py-1.5 text-center text-xs transition-colors duration-150 ${
                        isActive
                          ? "bg-surface-raised font-medium text-accent"
                          : "text-text-faint hover:text-text-muted"
                      }`
                    }
                  >
                    已結案
                  </NavLink>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function MobileBottomNavigation() {
  const { pathname } = useLocation();

  return (
    <nav
      aria-label="行動版主要功能"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-shell/95 px-2 pt-1.5 pb-[calc(env(safe-area-inset-bottom)+0.375rem)] shadow-[0_-12px_30px_oklch(0.12_0.01_165/0.12)] backdrop-blur-sm lg:hidden"
    >
      <ul className="mx-auto grid max-w-md grid-cols-5 gap-1">
        {navigationItems.map((item) => {
          const isCurrent = item.matches(pathname);
          const Icon = item.icon;

          return (
            <li key={item.to}>
              <NavLink
                to={item.to}
                aria-current={isCurrent ? "page" : undefined}
                className={`relative flex min-h-12 flex-col items-center justify-center gap-0.5 rounded-[10px] text-[0.6875rem] font-medium transition-colors duration-150 active:scale-[0.96] motion-reduce:transform-none ${
                  isCurrent
                    ? "bg-accent-soft text-accent"
                    : "text-text-faint hover:bg-card-hover hover:text-text-muted"
                }`}
              >
                <Icon className="h-5 w-5" />
                <span>{item.mobileLabel}</span>
                <span
                  className={`absolute bottom-0 h-0.5 w-5 rounded-full bg-accent transition-opacity duration-150 ${
                    isCurrent ? "opacity-100" : "opacity-0"
                  }`}
                  aria-hidden="true"
                />
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function PortfolioSubNavigation() {
  return (
    <nav
      aria-label="持股檢視"
      className="mb-5 inline-flex rounded-[10px] border border-border bg-shell p-1 lg:hidden"
    >
      <NavLink
        to="/portfolio"
        end
        className={({ isActive }) =>
          `flex min-h-10 items-center rounded-md px-4 text-sm font-medium transition-colors duration-150 ${
            isActive
              ? "bg-surface-raised text-accent shadow-panel"
              : "text-text-muted hover:text-text-primary"
          }`
        }
      >
        持有中
      </NavLink>
      <NavLink
        to="/portfolio/closed"
        className={({ isActive }) =>
          `flex min-h-10 items-center rounded-md px-4 text-sm font-medium transition-colors duration-150 ${
            isActive
              ? "bg-surface-raised text-accent shadow-panel"
              : "text-text-muted hover:text-text-primary"
          }`
        }
      >
        已結案
      </NavLink>
    </nav>
  );
}
