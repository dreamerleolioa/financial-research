import { useEffect, useId, useRef } from "react";
import { Link } from "react-router-dom";
import { formatDataMissingReason, formatMarketDataset, formatPriceAdjustmentMode } from "../../lib/presentationLabels";
import { type DailyRadarCandidate, type DailyRadarBackgroundContextLabel } from "../../lib/dailyRadarTypes";
import {
  formatDate,
  formatBucketLabel,
  formatRiskLabel,
  formatDataSourceLabel,
  formatTraceKey,
  formatBackgroundContextType,
  formatBackgroundFreshness,
  formatPhase1AvwapFreshness,
  formatPhase1AvwapMissingReason,
  backgroundLabelClass,
  formatMetric,
  formatSignedPct,
  getBackgroundContextUse,
  getCandidateDisplayTitle,
  formatMatchedRuleDetailKey,
  formatMatchedRuleValue,
  formatTraceValue,
  getActiveHTMLElement,
  isFocusableElement,
  getFocusableElements,
  getPhase1AvwapContext,
  getPhase1AvwapDisplayAnchors,
  formatPhase1AvwapDistanceLine,
} from "../../features/daily-radar/presentation";
import { CandidateResearchCard } from "./CandidateList";

function TraceValueList({
  payload,
  emptyText,
  formatKey = formatTraceKey,
  formatValue,
}: {
  payload: Record<string, unknown>;
  emptyText: string;
  formatKey?: (key: string) => string;
  formatValue?: (value: string) => string;
}) {
  const entries = Object.entries(payload).filter(([key]) => key !== "observation_score");

  if (entries.length === 0) {
    return (
      <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">{emptyText}</p>
    );
  }

  return (
    <dl className="grid gap-2 md:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
          <dt className="text-xs font-medium text-text-muted">{formatKey(key)}</dt>
          <dd className="mt-1 break-words text-sm text-text-primary">
            {formatTraceValue(value, formatKey, formatValue)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function BackgroundContextLabels({ labels }: { labels: DailyRadarBackgroundContextLabel[] }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-text-primary">背景脈絡</h3>
      <p className="mt-1 text-xs leading-relaxed text-text-muted">
        這些資料只用來補充研究背景與資料品質，不改變分類、分數或排序。
      </p>
      <div className="mt-3 grid gap-2">
        {labels.length > 0 ? (
          labels.map((label) => (
            <article
              key={`${label.context_type}:${label.replay_key}`}
              className={`rounded-lg border px-3 py-3 ${backgroundLabelClass(label)}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-current">
                    {formatBackgroundContextType(label.context_type)}
                  </p>
                  <p className="mt-1 text-xs opacity-75">{label.label}</p>
                </div>
                <span className="rounded-md bg-white/60 px-2 py-0.5 text-xs font-medium text-current dark:bg-white/10">
                  {formatBackgroundFreshness(label.freshness)}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-current">{getBackgroundContextUse(label)}</p>
              <p className="mt-2 text-xs opacity-70">
                資料日期：{formatDate(label.as_of_date)}
                {label.missing_reason ? `，缺資料原因：${formatDataMissingReason(label.missing_reason)}` : ""}
              </p>
            </article>
          ))
        ) : (
          <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
            尚未回傳背景脈絡標籤。
          </p>
        )}
      </div>
    </section>
  );
}

function Phase1AvwapContextPanel({ candidate }: { candidate: DailyRadarCandidate }) {
  const context = getPhase1AvwapContext(candidate);
  const anchors = context ? getPhase1AvwapDisplayAnchors(context) : [];
  const isMissing = !context || context.freshness === "missing" || Boolean(context.missing_reason);

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">試驗版 AVWAP 脈絡</h3>
          <p className="mt-1 text-xs leading-relaxed text-text-muted">
            只作為明細脈絡與資料品質參考，不改變 Daily Radar 排序、分類、分數或風險標籤。
          </p>
        </div>
        <span
          className={`rounded-md border px-2 py-0.5 text-xs font-medium ${
            isMissing
              ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
              : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
          }`}
        >
          {context ? formatPhase1AvwapFreshness(context.freshness) : "尚未回傳"}
        </span>
      </div>

      {context ? (
        <div className="mt-4 space-y-3">
          <div className="grid gap-2 md:grid-cols-3">
            <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
              <p className="text-xs font-medium text-text-muted">資料日期</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(context.data_date)}</p>
            </div>
            <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
              <p className="text-xs font-medium text-text-muted">資料集</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">{formatMarketDataset(context.dataset)}</p>
            </div>
            <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
              <p className="text-xs font-medium text-text-muted">調整模式</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">
                {formatPriceAdjustmentMode(context.adjustment_mode)}
              </p>
            </div>
          </div>

          {anchors.length > 0 ? (
            <div className="grid gap-2 md:grid-cols-2">
              {anchors.map((anchor) => (
                <article key={anchor.key} className="rounded-lg border border-border-subtle bg-surface px-3 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="text-xs font-semibold text-text-muted">{anchor.referenceLabel}</p>
                      {anchor.anchorDate && <p className="mt-1 text-xs text-text-faint">{anchor.anchorDate}</p>}
                    </div>
                    {anchor.estimated && (
                      <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                        日資料估算
                      </span>
                    )}
                  </div>
                  <div className="mt-3 grid gap-2">
                    <div>
                      <p className="text-xs text-text-faint">AVWAP 錨點值</p>
                      <p className="mt-0.5 font-mono text-lg font-semibold text-text-primary">
                        {formatMetric(anchor.avwap) ?? "—"}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border-subtle pt-2">
                      <p className="text-xs text-text-muted">{formatPhase1AvwapDistanceLine(anchor)}</p>
                      <p
                        className={`font-mono text-sm font-semibold ${
                          anchor.distance == null
                            ? "text-text-faint"
                            : anchor.distance >= 0
                              ? "text-emerald-600 dark:text-emerald-300"
                              : "text-red-600 dark:text-red-300"
                        }`}
                      >
                        {formatSignedPct(anchor.distance)}
                      </p>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-muted">
              {formatPhase1AvwapMissingReason(context.missing_reason)}
            </p>
          )}
        </div>
      ) : (
        <p className="mt-3 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
          這筆候選尚未包含試驗版 AVWAP 脈絡。
        </p>
      )}
    </section>
  );
}

function TechnicalTraceDetails({
  candidate,
  dataDateEntries,
}: {
  candidate: DailyRadarCandidate;
  dataDateEntries: [string, string][];
}) {
  return (
    <details className="rounded-xl border border-border bg-card p-4">
      <summary className="flex min-h-10 cursor-pointer items-center text-sm font-semibold text-text-primary">
        資料與規則細節
      </summary>
      <div className="mt-4 space-y-5">
        {candidate.explanation && (
          <section>
            <h4 className="text-sm font-semibold text-text-primary">系統觀察說明</h4>
            <p className="mt-2 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm leading-relaxed text-text-secondary">
              {candidate.explanation}
            </p>
          </section>
        )}

        <section>
          <h4 className="text-sm font-semibold text-text-primary">命中的觀察規則</h4>
          <div className="mt-3 space-y-3">
            {candidate.matched_rules.length > 0 ? (
              candidate.matched_rules.map((rule) => (
                <article key={rule.rule_id} className="rounded-lg border border-border-subtle bg-surface px-3 py-3">
                  <p className="text-sm font-semibold text-text-primary">{rule.label}</p>
                  <div className="mt-3">
                    <TraceValueList
                      payload={rule.details}
                      emptyText="此規則未附加細節。"
                      formatKey={formatMatchedRuleDetailKey}
                      formatValue={formatMatchedRuleValue}
                    />
                  </div>
                </article>
              ))
            ) : (
              <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
                尚未回傳命中的觀察規則。
              </p>
            )}
          </div>
        </section>

        <section>
          <h4 className="text-sm font-semibold text-text-primary">資料日期</h4>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {dataDateEntries.length > 0 ? (
              dataDateEntries.map(([source, date]) => (
                <div key={source} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                  <p className="text-xs font-medium text-text-muted">{formatDataSourceLabel(source)}</p>
                  <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(date)}</p>
                </div>
              ))
            ) : (
              <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint md:col-span-2">
                尚未回傳候選資料日期。
              </p>
            )}
          </div>
        </section>
      </div>
    </details>
  );
}

export function DailyRadarDetailDrawer({
  candidate,
  onClose,
}: {
  candidate: DailyRadarCandidate;
  onClose: () => void;
}) {
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
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/55" onClick={onClose}>
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="h-full w-full max-w-2xl overscroll-contain overflow-y-auto border-l border-border bg-surface-raised shadow-panel"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 z-10 border-b border-border-subtle bg-surface-raised px-5 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-text-faint">候選追蹤細節</p>
              <h2 id={titleId} className="mt-1 text-xl font-semibold text-text-primary">
                {getCandidateDisplayTitle(candidate)}
              </h2>
              <p className="mt-1 text-sm text-text-muted">追溯本次列入觀察清單的規則與資料輸入。</p>
              <Link
                to={`/analyze?symbol=${encodeURIComponent(candidate.symbol)}`}
                className="ui-button-secondary mt-3 min-h-10 px-3 text-xs"
              >
                前往單股完整分析
              </Link>
            </div>
            <button
              type="button"
              ref={closeButtonRef}
              onClick={onClose}
              className="ui-icon-button shrink-0 border border-border bg-surface-raised"
              aria-label="關閉候選追蹤細節"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-5 w-5"
                viewBox="0 0 20 20"
                fill="currentColor"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          </div>
        </div>

        <div className="space-y-5 px-5 py-5">
          <CandidateResearchCard candidate={candidate} />

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">觀察狀態</h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {candidate.secondary_buckets.length > 0 ? (
                candidate.secondary_buckets.map((bucket) => (
                  <span
                    key={bucket}
                    className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs font-medium text-badge-neutral-text"
                  >
                    次要分類：{formatBucketLabel(bucket)}
                  </span>
                ))
              ) : (
                <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                  無次要分類
                </span>
              )}
              {candidate.risk_labels.length > 0 ? (
                candidate.risk_labels.map((risk) => (
                  <span
                    key={risk}
                    className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                  >
                    {formatRiskLabel(risk)}
                  </span>
                ))
              ) : (
                <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                  未觸發明確風險標籤
                </span>
              )}
            </div>
          </section>

          <BackgroundContextLabels labels={candidate.background_context_labels} />

          <Phase1AvwapContextPanel candidate={candidate} />

          <TechnicalTraceDetails candidate={candidate} dataDateEntries={dataDateEntries} />
        </div>
      </aside>
    </div>
  );
}
