import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchLatestDailyRadarRun,
  toDailyRadarDisplayError,
  type DailyRadarDisplayError,
} from "../lib/dailyRadarApi";
import {
  DAILY_RADAR_BUCKETS,
  type DailyRadarBucket,
  type DailyRadarCandidate,
  type DailyRadarDateMap,
  type DailyRadarRepeatStatus,
  type DailyRadarRiskLabel,
  type DailyRadarRunResponse,
  type DailyRadarRunStatus,
} from "../lib/dailyRadarTypes";

const BUCKET_LABEL: Record<DailyRadarBucket, string> = {
  institutional_accumulation: "法人籌碼延續",
  price_volume_strengthening: "量價結構轉強",
  bottoming_reversal: "低位修復",
  support_retest: "支撐回測",
};

const RISK_LABEL: Record<DailyRadarRiskLabel, string> = {
  overextended: "短線過熱",
  flow_conflict: "量價籌碼分歧",
  margin_crowding: "融資偏擁擠",
  market_weakness: "大盤偏弱",
  data_gap: "資料缺口",
};

const REPEAT_STATUS_LABEL: Record<DailyRadarRepeatStatus, string> = {
  new: "首次列入觀察",
  repeat: "連續觀察中",
  upgraded: "觀察強度提升",
  cooled_down: "觀察降溫追蹤",
};

const REPEAT_STATUS_CLASS: Record<DailyRadarRepeatStatus, string> = {
  new: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  repeat: "bg-badge-neutral-bg text-badge-neutral-text",
  upgraded: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  cooled_down: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
};

const RUN_STATUS_LABEL: Record<DailyRadarRunStatus, string> = {
  completed: "掃描完成",
  running: "掃描中",
  failed: "掃描未完成",
  stale_data: "資料需留意",
};

function sortDailyRadarCandidates(candidates: DailyRadarCandidate[]): DailyRadarCandidate[] {
  return [...candidates].sort((a, b) => {
    const scoreDelta = b.observation_score - a.observation_score;
    if (scoreDelta !== 0) return scoreDelta;
    return a.symbol.localeCompare(b.symbol);
  });
}

function getBucketCounts(candidates: DailyRadarCandidate[]): Record<DailyRadarBucket, number> {
  return DAILY_RADAR_BUCKETS.reduce(
    (counts, bucket) => ({
      ...counts,
      [bucket]: candidates.filter((candidate) => candidate.primary_bucket === bucket).length,
    }),
    {} as Record<DailyRadarBucket, number>,
  );
}

function formatDate(value: string | null | undefined): string {
  return value || "—";
}

function formatDataSourceLabel(source: string): string {
  return source
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function isTraceRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatTraceKey(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatTraceValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString() : String(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return value.length > 96 ? `${value.slice(0, 96)}…` : value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    const preview = value.slice(0, 4).map(formatTraceValue).join("、");
    return value.length > 4 ? `${preview}，另 ${value.length - 4} 項` : preview;
  }
  if (isTraceRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return "—";
    const preview = entries
      .slice(0, 3)
      .map(([key, nestedValue]) => `${formatTraceKey(key)}：${formatTraceValue(nestedValue)}`)
      .join("；");
    return entries.length > 3 ? `${preview}；另 ${entries.length - 3} 項` : preview;
  }
  return String(value);
}

const FOCUSABLE_ELEMENT_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function getActiveHTMLElement(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

function isFocusableElement(element: HTMLElement | null): element is HTMLElement {
  if (!element?.isConnected) return false;
  if (element.matches("[disabled], [aria-disabled='true']")) return false;

  if (typeof window !== "undefined") {
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
  }

  return element.matches(FOCUSABLE_ELEMENT_SELECTOR);
}

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_ELEMENT_SELECTOR)).filter(isFocusableElement);
}

function getFreshnessSummary(runDate: string | null | undefined, dataDates: DailyRadarDateMap): string {
  const dates = Object.values(dataDates).filter(Boolean);
  if (dates.length === 0) return "資料日期尚未回傳，請留意後續同步狀態。";

  const latestDate = dates.reduce((latest, date) => (date > latest ? date : latest), dates[0]);
  if (!runDate) return `資料最新日期 ${latestDate}。`;

  const laggingCount = dates.filter((date) => date < runDate).length;
  if (laggingCount === 0) return "資料日期與最新掃描日一致。";

  return `資料最新日期 ${latestDate}，${laggingCount} 項資料早於掃描日。`;
}

function hasLaggingRunData(runDate: string | null | undefined, dataDates: DailyRadarDateMap): boolean {
  if (!runDate) return false;
  return Object.values(dataDates).some((date) => Boolean(date) && date < runDate);
}

function StatCard({ label, value, helper }: { label: string; value: string; helper?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs font-medium text-text-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-text-primary">{value}</p>
      {helper && <p className="mt-1 text-xs text-text-faint">{helper}</p>}
    </div>
  );
}

function DailyRadarBucketTabs({
  selectedBucket,
  counts,
  totalCount,
  onSelectBucket,
}: {
  selectedBucket: DailyRadarBucket | null;
  counts: Record<DailyRadarBucket, number>;
  totalCount: number;
  onSelectBucket: (bucket: DailyRadarBucket | null) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3 shadow-sm">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="候選觀察分類">
        <button
          type="button"
          role="tab"
          aria-selected={selectedBucket === null}
          onClick={() => onSelectBucket(null)}
          className={`rounded-lg px-3 py-2 text-left text-sm font-medium transition ${selectedBucket === null
            ? "bg-indigo-600 text-white shadow-sm"
            : "border border-border bg-card text-text-muted hover:bg-card-hover hover:text-text-secondary"
            }`}
        >
          <span>全部候選</span>
          <span className="ml-2 rounded-full bg-white/20 px-2 py-0.5 text-xs">{totalCount}</span>
        </button>
        {DAILY_RADAR_BUCKETS.map((bucket) => {
          const active = selectedBucket === bucket;
          return (
            <button
              key={bucket}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onSelectBucket(bucket)}
              className={`rounded-lg px-3 py-2 text-left text-sm font-medium transition ${active
                ? "bg-indigo-600 text-white shadow-sm"
                : "border border-border bg-card text-text-muted hover:bg-card-hover hover:text-text-secondary"
                }`}
            >
              <span>{BUCKET_LABEL[bucket]}</span>
              <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${active ? "bg-white/20" : "bg-badge-neutral-bg text-badge-neutral-text"}`}>
                {counts[bucket]}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TraceValueList({ payload, emptyText }: { payload: Record<string, unknown>; emptyText: string }) {
  const entries = Object.entries(payload);

  if (entries.length === 0) {
    return <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">{emptyText}</p>;
  }

  return (
    <dl className="grid gap-2 md:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
          <dt className="text-xs font-medium text-text-muted">{formatTraceKey(key)}</dt>
          <dd className="mt-1 break-words text-sm text-text-primary">{formatTraceValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function DailyRadarCandidateList({
  candidates,
  onSelectCandidate,
}: {
  candidates: DailyRadarCandidate[];
  onSelectCandidate: (candidate: DailyRadarCandidate) => void;
}) {
  if (candidates.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center text-sm text-text-faint shadow-sm">
        目前此分類沒有通過濾網的觀察候選。
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">候選觀察清單</h2>
          <p className="mt-1 text-xs text-text-muted">依觀察分數排序，分數相同時依代號排序。</p>
        </div>
        <span className="rounded-full bg-badge-neutral-bg px-3 py-1 text-xs font-medium text-badge-neutral-text">
          共 {candidates.length} 筆
        </span>
      </div>
      <div className="divide-y divide-border-subtle">
        {candidates.map((candidate) => (
          <button
            key={candidate.symbol}
            type="button"
            aria-haspopup="dialog"
            onClick={() => onSelectCandidate(candidate)}
            className="block w-full px-4 py-4 text-left transition hover:bg-card-hover focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-400"
          >
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-mono text-base font-semibold text-text-primary">{candidate.symbol}</p>
                  <p className="text-sm font-medium text-text-secondary">{candidate.name}</p>
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${REPEAT_STATUS_CLASS[candidate.repeat_status]}`}>
                    {REPEAT_STATUS_LABEL[candidate.repeat_status]}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs font-medium text-badge-neutral-text">
                    {BUCKET_LABEL[candidate.primary_bucket]}
                  </span>
                  {candidate.risk_labels.length > 0 ? (
                    candidate.risk_labels.map((risk) => (
                      <span key={risk} className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                        {RISK_LABEL[risk]}
                      </span>
                    ))
                  ) : (
                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                      風險標籤待觀察
                    </span>
                  )}
                </div>
              </div>
              <div className="rounded-xl border border-border-subtle bg-surface px-4 py-3 text-right md:min-w-28">
                <p className="text-xs font-medium text-text-muted">觀察分數</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-text-primary">
                  {candidate.observation_score.toFixed(0)}
                </p>
              </div>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function DailyRadarDetailDrawer({ candidate, onClose }: { candidate: DailyRadarCandidate; onClose: () => void }) {
  const titleId = useId();
  const drawerRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(getActiveHTMLElement());

  useEffect(() => {
    const previouslyFocusedElement = previouslyFocusedElementRef.current;
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab") return;

      const drawer = drawerRef.current;
      if (!drawer) return;

      const focusableElements = getFocusableElements(drawer);
      if (focusableElements.length === 0) {
        event.preventDefault();
        drawer.focus();
        return;
      }

      const firstFocusableElement = focusableElements[0];
      const lastFocusableElement = focusableElements[focusableElements.length - 1];
      const activeElement = getActiveHTMLElement();
      const focusIsOutsideDrawer = !activeElement || !drawer.contains(activeElement);

      if (event.shiftKey) {
        if (focusIsOutsideDrawer || activeElement === firstFocusableElement) {
          event.preventDefault();
          lastFocusableElement.focus();
        }
        return;
      }

      if (focusIsOutsideDrawer || activeElement === lastFocusableElement) {
        event.preventDefault();
        firstFocusableElement.focus();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (isFocusableElement(previouslyFocusedElement)) previouslyFocusedElement.focus();
    };
  }, [onClose]);

  const dataDateEntries = Object.entries(candidate.data_dates);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/45 backdrop-blur-sm" onClick={onClose}>
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-border bg-card shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 z-10 border-b border-border-subtle bg-card/95 px-5 py-4 backdrop-blur">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-text-faint">候選追蹤細節</p>
              <h2 id={titleId} className="mt-1 text-xl font-semibold text-text-primary">
                {candidate.symbol} · {candidate.name}
              </h2>
              <p className="mt-1 text-sm text-text-muted">追溯本次列入觀察清單的分數、規則與資料輸入。</p>
            </div>
            <button
              type="button"
              ref={closeButtonRef}
              onClick={onClose}
              className="rounded-lg p-2 text-text-faint transition hover:bg-card-hover hover:text-text-secondary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              aria-label="關閉候選追蹤細節"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </button>
          </div>
        </div>

        <div className="space-y-5 px-5 py-5">
          <section className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-border bg-surface p-4">
              <p className="text-xs font-medium text-text-muted">觀察分數</p>
              <p className="mt-2 font-mono text-3xl font-semibold text-text-primary">{candidate.observation_score.toFixed(0)}</p>
            </div>
            <div className="rounded-xl border border-border bg-surface p-4 md:col-span-2">
              <p className="text-xs font-medium text-text-muted">主要分類</p>
              <p className="mt-2 text-sm font-semibold text-text-primary">{BUCKET_LABEL[candidate.primary_bucket]}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {candidate.secondary_buckets.length > 0 ? (
                  candidate.secondary_buckets.map((bucket) => (
                    <span key={bucket} className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs font-medium text-badge-neutral-text">
                      {BUCKET_LABEL[bucket]}
                    </span>
                  ))
                ) : (
                  <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">無次要分類</span>
                )}
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <div className="flex flex-wrap gap-2">
              <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${REPEAT_STATUS_CLASS[candidate.repeat_status]}`}>
                {REPEAT_STATUS_LABEL[candidate.repeat_status]}
              </span>
              {candidate.risk_labels.length > 0 ? (
                candidate.risk_labels.map((risk) => (
                  <span key={risk} className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                    {RISK_LABEL[risk]}
                  </span>
                ))
              ) : (
                <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">風險標籤待觀察</span>
              )}
            </div>
            <div className="mt-4 rounded-lg border border-border-subtle bg-surface px-3 py-3">
              <p className="text-xs font-medium text-text-muted">隔日觀察重點</p>
              <p className="mt-2 text-sm leading-relaxed text-text-secondary">{candidate.explanation || "尚未回傳觀察說明。"}</p>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">Bucket 分數</h3>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {DAILY_RADAR_BUCKETS.map((bucket) => (
                <div key={bucket} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                  <p className="text-xs font-medium text-text-muted">{BUCKET_LABEL[bucket]}</p>
                  <p className="mt-1 font-mono text-lg font-semibold text-text-primary">
                    {candidate.bucket_scores[bucket] === undefined ? "—" : candidate.bucket_scores[bucket]?.toFixed(0)}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">Score breakdown</h3>
            <div className="mt-3">
              <TraceValueList payload={candidate.score_breakdown} emptyText="尚未回傳分數拆解。" />
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">Matched rules</h3>
            <div className="mt-3 space-y-3">
              {candidate.matched_rules.length > 0 ? (
                candidate.matched_rules.map((rule) => (
                  <article key={rule.rule_id} className="rounded-lg border border-border-subtle bg-surface px-3 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-text-primary">{rule.label}</p>
                      <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 font-mono text-xs text-badge-neutral-text">{rule.rule_id}</span>
                    </div>
                    <div className="mt-3">
                      <TraceValueList payload={rule.details} emptyText="此規則未附加細節。" />
                    </div>
                  </article>
                ))
              ) : (
                <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">尚未回傳命中的觀察規則。</p>
              )}
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">資料日期</h3>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {dataDateEntries.length > 0 ? (
                dataDateEntries.map(([source, date]) => (
                  <div key={source} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                    <p className="text-xs font-medium text-text-muted">{formatDataSourceLabel(source)}</p>
                    <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(date)}</p>
                  </div>
                ))
              ) : (
                <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint md:col-span-2">尚未回傳候選資料日期。</p>
              )}
            </div>
          </section>

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">Input snapshot 摘要</h3>
            <div className="mt-3">
              <TraceValueList payload={candidate.input_snapshot} emptyText="尚未回傳輸入快照。" />
            </div>
          </section>

          <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4 dark:border-indigo-900 dark:bg-indigo-950">
            <p className="text-sm leading-relaxed text-indigo-900 dark:text-indigo-200">
              若需要延伸檢視單一標的的資料脈絡，可前往個股完整分析頁重新整理觀察素材。
            </p>
            <Link
              to="/analyze"
              className="mt-3 inline-flex rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 dark:focus:ring-offset-slate-900"
            >
              查看單股完整分析
            </Link>
          </div>
        </div>
      </aside>
    </div>
  );
}

function LoadingState() {
  return (
    <section className="rounded-xl border border-border bg-card p-6 text-center shadow-sm">
      <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-border border-t-indigo-600 dark:border-border dark:border-t-indigo-400" />
      <p className="mt-4 text-sm font-medium text-text-primary">資料載入中</p>
      <p className="mt-1 text-xs text-text-muted">正在讀取最新盤後觀察雷達，完成後會顯示掃描日期與資料新鮮度。</p>
    </section>
  );
}

function ErrorState({ error, onRetry }: { error: DailyRadarDisplayError; onRetry: () => void }) {
  return (
    <section className="rounded-xl border border-red-200 bg-red-50 p-6 shadow-sm dark:border-red-900 dark:bg-red-950">
      <p className="text-sm font-semibold text-red-700 dark:text-red-300">Daily Radar 暫時無法載入</p>
      <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error.message}</p>
      {error.status && (
        <p className="mt-1 text-xs text-red-600 dark:text-red-400">狀態碼：{error.status}</p>
      )}
      <button
        onClick={onRetry}
        className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700"
      >
        重新讀取觀察資料
      </button>
    </section>
  );
}

function StaleRunDataNotice({ runDate, freshnessSummary }: { runDate: string; freshnessSummary: string }) {
  return (
    <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm dark:border-amber-800 dark:bg-amber-950">
      <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">資料日期落後掃描日</p>
      <p className="mt-2 text-sm leading-relaxed text-amber-800 dark:text-amber-300">
        本批次掃描日為 {formatDate(runDate)}，但部分資料日期較晚同步或仍落後；{freshnessSummary}
        結果應作為觀察追蹤與風險脈絡，不代表當前高信心排序。
      </p>
    </section>
  );
}

function WholeRunEmptyState() {
  return (
    <section className="rounded-xl border border-border bg-card p-6 text-center shadow-sm">
      <p className="text-sm font-semibold text-text-primary">今日沒有通過濾網的高品質 setup。</p>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-text-muted">
        這代表本次規則掃描沒有產生符合觀察門檻的追蹤候選，並非系統失敗；可持續留意後續資料同步與風險脈絡變化。
      </p>
    </section>
  );
}

function RunSummary({ run }: { run: DailyRadarRunResponse }) {
  const [selectedBucket, setSelectedBucket] = useState<DailyRadarBucket | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<DailyRadarCandidate | null>(null);
  const dataDateEntries = Object.entries(run.data_dates);
  const freshnessSummary = getFreshnessSummary(run.run_date, run.data_dates);
  const shouldShowStaleNotice = run.status === "stale_data" || hasLaggingRunData(run.run_date, run.data_dates);
  const sortedCandidates = sortDailyRadarCandidates(run.candidates);
  const bucketCounts = getBucketCounts(sortedCandidates);
  const visibleCandidates = selectedBucket
    ? sortedCandidates.filter((candidate) => candidate.primary_bucket === selectedBucket)
    : sortedCandidates;

  return (
    <>
      <section className="grid gap-3 md:grid-cols-4">
        <StatCard label="最新掃描日" value={formatDate(run.run_date)} helper="盤後觀察批次" />
        <StatCard label="掃描狀態" value={RUN_STATUS_LABEL[run.status]} helper={run.status} />
        <StatCard label="觀察候選數" value={String(run.candidates.length)} helper="符合規則的追蹤名單" />
        <StatCard label="資料新鮮度" value={dataDateEntries.length > 0 ? "已回傳" : "待確認"} helper={freshnessSummary} />
      </section>

      {shouldShowStaleNotice && <StaleRunDataNotice runDate={run.run_date} freshnessSummary={freshnessSummary} />}

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">資料日期與追蹤狀態</h2>
            <p className="mt-1 text-xs text-text-muted">用於確認各資料源是否已同步到本次掃描。</p>
          </div>
          <span className="rounded-full bg-badge-neutral-bg px-3 py-1 text-xs font-medium text-badge-neutral-text">
            {freshnessSummary}
          </span>
        </div>

        <div className="mt-4 grid gap-2 md:grid-cols-3">
          {dataDateEntries.length > 0 ? (
            dataDateEntries.map(([source, date]) => (
              <div key={source} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                <p className="text-xs font-medium text-text-muted">{formatDataSourceLabel(source)}</p>
                <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(date)}</p>
              </div>
            ))
          ) : (
            <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint md:col-span-3">
              尚未收到資料日期，請稍後重新讀取觀察資料。
            </p>
          )}
        </div>
      </section>

      {sortedCandidates.length === 0 ? (
        <WholeRunEmptyState />
      ) : (
        <>
          <DailyRadarBucketTabs
            selectedBucket={selectedBucket}
            counts={bucketCounts}
            totalCount={sortedCandidates.length}
            onSelectBucket={setSelectedBucket}
          />

          <DailyRadarCandidateList candidates={visibleCandidates} onSelectCandidate={setSelectedCandidate} />
        </>
      )}

      {selectedCandidate && (
        <DailyRadarDetailDrawer candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} />
      )}
    </>
  );
}

export default function DailyRadarPage() {
  const [run, setRun] = useState<DailyRadarRunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<DailyRadarDisplayError | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadLatestRun() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchLatestDailyRadarRun();
        if (!cancelled) setRun(data);
      } catch (err) {
        if (!cancelled) setError(toDailyRadarDisplayError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadLatestRun();

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return (
    <div className="space-y-6 px-6 py-4">
      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-faint">Daily Radar</p>
        <div className="mt-2 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-primary md:text-2xl">每日盤後觀察雷達</h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-muted">
              以固定規則追蹤量價、籌碼與資料新鮮度，協助隔日觀察清單整理；內容僅供風險與訊號追蹤。
            </p>
          </div>
          <button
            onClick={() => setReloadKey((key) => key + 1)}
            disabled={loading}
            className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium text-text-muted transition hover:bg-card-hover hover:text-text-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "讀取中…" : "重新整理"}
          </button>
        </div>
      </section>

      {loading ? <LoadingState /> : error ? <ErrorState error={error} onRetry={() => setReloadKey((key) => key + 1)} /> : run ? <RunSummary run={run} /> : null}
    </div>
  );
}
