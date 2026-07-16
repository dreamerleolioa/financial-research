import type { ReactNode } from "react";
import { ProductBrand } from "../brand/ProductBrand";
import { useDarkMode } from "../../stores/theme";

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

export function AuthenticationShell({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  const { theme, toggle } = useDarkMode();
  const themeLabel = theme === "dark" ? "切換為亮色模式" : "切換為暗色模式";

  return (
    <main className="min-h-dvh bg-canvas text-text-primary lg:grid lg:grid-cols-[minmax(22rem,0.88fr)_minmax(28rem,1.12fr)]">
      <section className="hidden min-h-dvh flex-col justify-between border-r border-border bg-shell px-10 py-9 lg:flex xl:px-14 xl:py-12">
        <ProductBrand to="/login" />

        <div className="max-w-xl">
          <p className="text-xs font-semibold tracking-[0.16em] text-accent uppercase">Research with discipline</p>
          <p className="mt-4 text-3xl font-semibold leading-tight text-text-primary">
            把每日資料整理成可追蹤、可回顧的研究流程。
          </p>
          <div className="mt-8 grid gap-4">
            {[
              ["01", "先看資料位置", "用快速資料確認行情、技術指標與 AVWAP。"],
              ["02", "再整理風險", "需要時補上完整分析，保留觀察條件與紀律。"],
              ["03", "持續追蹤", "將標的放入關注或持股工作區，累積自己的研究脈絡。"],
            ].map(([step, itemTitle, itemDescription]) => (
              <div key={step} className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-3 border-t border-border pt-4">
                <span className="text-xs font-semibold tabular-nums text-accent">{step}</span>
                <div>
                  <p className="text-sm font-semibold text-text-primary">{itemTitle}</p>
                  <p className="mt-1 text-sm leading-relaxed text-text-muted">{itemDescription}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="text-xs leading-relaxed text-text-faint">個人研究工作區，所有分析仍需由使用者自行判斷。</p>
      </section>

      <section className="flex min-h-dvh flex-col">
        <header className="flex h-16 items-center justify-between border-b border-border px-4 sm:px-7 lg:justify-end lg:border-b-0">
          <div className="lg:hidden">
            <ProductBrand compact to="/login" />
          </div>
          <button type="button" onClick={toggle} className="ui-icon-button" aria-label={themeLabel} title={themeLabel}>
            <ThemeIcon theme={theme} />
          </button>
        </header>

        <div className="flex flex-1 items-center px-4 py-10 sm:px-8 lg:px-12 xl:px-20">
          <div className="mx-auto w-full max-w-[28rem]">
            <p className="text-xs font-semibold tracking-[0.16em] text-accent uppercase">{eyebrow}</p>
            <h1 className="mt-3 text-3xl font-semibold leading-tight text-text-primary">{title}</h1>
            <p className="mt-3 text-sm leading-relaxed text-text-muted">{description}</p>
            <div className="mt-8">{children}</div>
          </div>
        </div>
      </section>
    </main>
  );
}
