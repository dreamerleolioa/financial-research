import { type DailyRadarCandidate } from "../../lib/dailyRadarTypes";
import {
  formatBucketLabel,
  formatRiskLabel,
  formatMembershipLabel,
  formatObservationHistory,
  formatSignalStatus,
  formatMediumTermObservation,
  getRepeatStatusClass,
  getBucketResearchThesis,
  getBucketInvalidationHint,
  getCandidateReasonHighlights,
  getCandidateWatchItems,
  getCandidateDisplayName,
} from "../../features/daily-radar/presentation";

export function CandidateResearchCard({ candidate }: { candidate: DailyRadarCandidate }) {
  const reasons = getCandidateReasonHighlights(candidate);
  const watchItems = getCandidateWatchItems(candidate);

  return (
    <section className="rounded-xl border border-accent/30 bg-accent-soft/35 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${getRepeatStatusClass(candidate.repeat_status)}`}
        >
          {formatMembershipLabel(candidate)}
        </span>
        {formatSignalStatus(candidate) && <span className="text-xs text-text-secondary">{formatSignalStatus(candidate)}</span>}
        <span className="rounded-md bg-surface-raised/75 px-2 py-0.5 text-xs font-medium text-accent">
          {formatBucketLabel(candidate.primary_bucket)}候選
        </span>
      </div>
      {formatObservationHistory(candidate) && <p className="mt-2 text-xs text-text-muted">{formatObservationHistory(candidate)}</p>}
      {formatMediumTermObservation(candidate) && <p className="mt-2 text-xs text-text-secondary">{formatMediumTermObservation(candidate)}</p>}
      <h3 className="mt-3 text-base font-semibold text-text-primary">
        {formatBucketLabel(candidate.primary_bucket)}候選，僅供觀察追蹤
      </h3>
      <p className="mt-2 text-sm leading-relaxed text-text-secondary">
        {getBucketResearchThesis(candidate.primary_bucket)}
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-border-subtle bg-card/80 px-3 py-3">
          <p className="text-xs font-semibold text-text-muted">本次入選原因</p>
          {reasons.length > 0 ? (
            <ul className="mt-2 space-y-2 text-sm leading-relaxed text-text-primary">
              {reasons.map((reason) => (
                <li key={reason} className="flex gap-2">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden="true" />
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-text-faint">尚未回傳可讀的入選原因。</p>
          )}
        </div>
        <div className="rounded-lg border border-border-subtle bg-card/80 px-3 py-3">
          <p className="text-xs font-semibold text-text-muted">隔日觀察點</p>
          <ul className="mt-2 space-y-2 text-sm leading-relaxed text-text-primary">
            {watchItems.map((item) => (
              <li key={item} className="flex gap-2">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-positive" aria-hidden="true" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-border-subtle bg-card/80 px-3 py-3">
        <p className="text-xs font-semibold text-text-muted">失效條件</p>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">
          {getBucketInvalidationHint(candidate.primary_bucket)}
        </p>
      </div>
    </section>
  );
}

export function DailyRadarCandidateList({
  candidates,
  onSelectCandidate,
}: {
  candidates: DailyRadarCandidate[];
  onSelectCandidate: (candidate: DailyRadarCandidate) => void;
}) {
  if (candidates.length === 0) {
    return (
      <div className="rounded-[14px] border border-border bg-surface-raised p-6 text-center text-sm text-text-faint shadow-panel">
        目前此分類沒有通過濾網的觀察候選。
      </div>
    );
  }

  return (
    <section className="overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel">
      <div className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">候選觀察清單</h2>
          <p className="mt-1 text-xs text-text-muted">依系統內部排序排列；排序不代表勝率或交易建議。</p>
        </div>
        <span className="ui-badge shrink-0">共 {candidates.length} 筆</span>
      </div>

      <div className="hidden grid-cols-[minmax(180px,1.35fr)_minmax(120px,0.8fr)_minmax(150px,1fr)_minmax(150px,1fr)_auto] gap-4 border-b border-border-subtle bg-surface px-5 py-2.5 text-xs font-medium text-text-faint md:grid">
        <span>標的</span>
        <span>追蹤狀態</span>
        <span>主要分類</span>
        <span>風險</span>
        <span className="text-right">操作</span>
      </div>

      <div className="grid gap-3 bg-canvas/45 p-3 md:block md:divide-y md:divide-border-subtle md:bg-transparent md:p-0">
        {candidates.map((candidate) => {
          const displayName = getCandidateDisplayName(candidate);
          return (
            <article
              key={candidate.symbol}
              data-daily-radar-candidate={candidate.symbol}
              className="grid grid-cols-2 gap-x-4 gap-y-4 rounded-[12px] border border-border bg-surface-raised p-4 shadow-panel transition-colors hover:bg-card-hover focus-within:bg-card-hover md:grid-cols-[minmax(180px,1.35fr)_minmax(120px,0.8fr)_minmax(150px,1fr)_minmax(150px,1fr)_auto] md:items-center md:rounded-none md:border-0 md:px-5 md:py-4 md:shadow-none"
            >
              <div className="col-span-2 min-w-0 md:col-span-1">
                <p className="text-xs font-medium text-text-faint md:hidden">標的</p>
                <button
                  type="button"
                  aria-haspopup="dialog"
                  onClick={() => onSelectCandidate(candidate)}
                  className="mt-1 min-h-10 max-w-full rounded-[8px] text-left md:mt-0"
                >
                  <span className="block truncate text-sm font-semibold text-text-primary">
                    {displayName ?? candidate.symbol}
                  </span>
                  {displayName && (
                    <span className="mt-0.5 block font-mono text-xs font-medium text-text-muted">
                      {candidate.symbol}
                    </span>
                  )}
                </button>
              </div>

              <div className="min-w-0">
                <p className="text-xs font-medium text-text-faint md:hidden">追蹤狀態</p>
                <span
                  className={`mt-1 inline-flex rounded-full px-2.5 py-1 text-xs font-medium md:mt-0 ${getRepeatStatusClass(candidate.repeat_status)}`}
                >
                  {formatMembershipLabel(candidate)}
                </span>
                {formatObservationHistory(candidate) && <p className="mt-1 text-xs text-text-muted">{formatObservationHistory(candidate)}</p>}
                {formatSignalStatus(candidate) && <p className="mt-1 text-xs text-text-secondary">{formatSignalStatus(candidate)}</p>}
                {formatMediumTermObservation(candidate) && <p className="mt-1 text-xs text-text-secondary">{formatMediumTermObservation(candidate)}</p>}
              </div>

              <div className="min-w-0">
                <p className="text-xs font-medium text-text-faint md:hidden">主要分類</p>
                <p className="mt-1 text-sm font-medium leading-snug text-text-secondary md:mt-0">
                  {formatBucketLabel(candidate.primary_bucket)}
                </p>
              </div>

              <div className="col-span-2 min-w-0 md:col-span-1">
                <p className="text-xs font-medium text-text-faint md:hidden">風險</p>
                <div className="mt-1 flex flex-wrap gap-1.5 md:mt-0">
                  {candidate.risk_labels.length > 0 ? (
                    candidate.risk_labels.map((risk) => (
                      <span
                        key={risk}
                        className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                      >
                        {formatRiskLabel(risk)}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-text-faint">未觸發明確風險</span>
                  )}
                </div>
              </div>

              <div className="col-span-2 flex flex-wrap justify-end gap-2 md:col-span-1 md:flex-nowrap">
                <button
                  type="button"
                  onClick={() => onSelectCandidate(candidate)}
                  className="ui-button-primary min-h-10 px-3 text-xs"
                >
                  查看細節
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
