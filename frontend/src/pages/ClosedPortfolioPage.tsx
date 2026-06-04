import { useEffect, useMemo, useState } from "react";
import { authHeaders } from "../lib/auth";
import { formatPrice } from "../lib/formatters";
import type { ClosedPortfolioItem } from "../lib/portfolioTypes";

type PeriodKey = "1d" | "1w" | "1m" | "1q" | "1y";

interface PeriodOption {
  key: PeriodKey;
  label: string;
  days: number;
}

const PERIOD_OPTIONS: PeriodOption[] = [
  { key: "1d", label: "1天", days: 1 },
  { key: "1w", label: "1週", days: 7 },
  { key: "1m", label: "1月", days: 30 },
  { key: "1q", label: "1季", days: 90 },
  { key: "1y", label: "1年", days: 365 },
];

const SELECTED_PERIOD_STORAGE_KEY = "closedPortfolio.selectedPeriod";

function isValidPeriodKey(value: string | null): value is PeriodKey {
  return PERIOD_OPTIONS.some((option) => option.key === value);
}

function readStoredPeriod(): PeriodKey {
  try {
    const storedPeriod = window.localStorage.getItem(SELECTED_PERIOD_STORAGE_KEY);
    return isValidPeriodKey(storedPeriod) ? storedPeriod : "1m";
  } catch {
    return "1m";
  }
}

function writeStoredPeriod(period: PeriodKey): boolean {
  try {
    window.localStorage.setItem(SELECTED_PERIOD_STORAGE_KEY, period);
    return true;
  } catch {
    return false;
  }
}

function toLocalDate(value: string): Date | null {
  const [yearText, monthText, dayText] = value.slice(0, 10).split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null;
  return new Date(year, month - 1, day);
}

function getToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function isWithinPeriod(exitDate: string, days: number, today: Date): boolean {
  const date = toLocalDate(exitDate);
  if (!date) return false;
  const start = new Date(today);
  start.setDate(today.getDate() - days + 1);
  return date >= start && date <= today;
}

export default function ClosedPortfolioPage() {
  const [items, setItems] = useState<ClosedPortfolioItem[]>([]);
  const [selectedPeriod, setSelectedPeriod] = useState<PeriodKey>(readStoredPeriod);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    writeStoredPeriod(selectedPeriod);
  }, [selectedPeriod]);

  useEffect(() => {
    let cancelled = false;

    async function loadClosedPortfolio() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${import.meta.env.VITE_API_URL}/portfolio/closed`, {
          headers: authHeaders(),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: ClosedPortfolioItem[] = await res.json();
        if (!cancelled) setItems(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "已結案持股載入失敗");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadClosedPortfolio();
    return () => {
      cancelled = true;
    };
  }, []);

  const activePeriod = PERIOD_OPTIONS.find((option) => option.key === selectedPeriod) ?? PERIOD_OPTIONS[2];
  const filteredItems = useMemo(() => {
    const today = getToday();
    return items.filter((item) => isWithinPeriod(item.exit_date, activePeriod.days, today));
  }, [activePeriod.days, items]);

  const totalRealizedPnl = useMemo(
    () => filteredItems.reduce((total, item) => total + item.realized_pnl, 0),
    [filteredItems],
  );
  const totalClass = totalRealizedPnl >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="h-3 w-20 animate-pulse rounded bg-border" />
          <div className="mt-3 h-8 w-40 animate-pulse rounded bg-border" />
        </div>
        {[1, 2].map((item) => (
          <div key={item} className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm">
            <div className="h-5 w-24 animate-pulse rounded bg-border" />
            <div className="mt-2 h-3 w-48 animate-pulse rounded bg-border" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-xs font-medium text-text-faint">{activePeriod.label} 已實現損益</p>
            <p className={`mt-1 font-mono text-3xl font-semibold ${totalClass}`}>
              {totalRealizedPnl >= 0 ? "+" : ""}{formatPrice(totalRealizedPnl)}
            </p>
            <p className="mt-1 text-xs text-text-muted">篩選 {filteredItems.length} 筆已結案紀錄</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {PERIOD_OPTIONS.map((option) => {
              const isActive = option.key === selectedPeriod;
              return (
                <button
                  key={option.key}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => setSelectedPeriod(option.key)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${isActive
                    ? "bg-indigo-600 text-white"
                    : "border border-border bg-card text-text-muted hover:bg-card-hover"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-sm dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-border bg-card shadow-sm">
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <h2 className="text-sm font-semibold text-text-primary">已結案持股</h2>
          <span className="text-xs text-text-faint">共 {filteredItems.length} 筆</span>
        </div>

        {filteredItems.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-text-faint">
            此期間尚無已結案持股。
          </div>
        ) : (
          <div className="divide-y divide-border-subtle">
            {filteredItems.map((item) => {
              const isProfit = item.realized_pnl >= 0;
              const resultClass = isProfit ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
              return (
                <article key={item.id} className="px-4 py-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-semibold text-text-primary">{item.symbol}</p>
                        <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                          {item.entry_date} → {item.exit_date}
                        </span>
                        <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                          持有 {item.holding_days} 天
                        </span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-text-muted">
                        <span>
                          {formatPrice(item.entry_price, item.symbol)} → {formatPrice(item.exit_price, item.symbol)}
                        </span>
                        <span>出場 {item.exit_quantity} 股</span>
                        <span>
                          費稅 {formatPrice(item.exit_fees + item.exit_taxes, item.symbol)}
                        </span>
                      </div>
                    </div>
                    <div className="text-left sm:text-right">
                      <p className={`font-mono text-sm font-semibold ${resultClass}`}>
                        {isProfit ? "+" : ""}{formatPrice(item.realized_pnl, item.symbol)}
                      </p>
                      <p className={`font-mono text-xs ${resultClass}`}>
                        {item.realized_return_pct > 0 ? "+" : ""}{item.realized_return_pct.toFixed(2)}%
                      </p>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
