import { useState } from "react";
import {
  DAILY_RADAR_BUCKETS,
  type DailyRadarBucket,
  type DailyRadarCandidate,
  type DailyRadarRunResponse,
} from "../../lib/dailyRadarTypes";
import {
  BUCKET_LABEL,
  sortDailyRadarCandidates,
  getBucketCounts,
  formatDate,
  formatRunStatusLabel,
  formatRunStatusHelper,
  formatDataSourceLabel,
  getFreshnessSummary,
  hasLaggingRunData,
  getRunStatusClass,
} from "../../features/daily-radar/presentation";
import { DailyRadarCandidateList } from "./CandidateList";
import { DailyRadarDetailDrawer } from "./CandidateDetailDrawer";
import { WholeRunEmptyState } from "./QueryStates";

function RunMetric({
  label,
  value,
  helper,
  valueClass = "text-text-primary",
}: {
  label: string;
  value: string;
  helper?: string;
  valueClass?: string;
}) {
  return (
    <div className="border-t border-border-subtle px-4 py-4 md:px-5 lg:border-t-0 lg:border-l lg:first:border-l-0">
      <p className="text-xs font-medium text-text-faint">{label}</p>
      <p className={`mt-2 text-lg font-semibold tabular-nums ${valueClass}`}>{value}</p>
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
    <div className="sticky top-14 z-20 bg-canvas/95 py-3 lg:top-0">
      <div className="overflow-x-auto rounded-[12px] border border-border bg-surface-raised p-1.5 shadow-panel">
        <div className="flex min-w-max gap-1.5" role="tablist" aria-label="候選觀察分類">
          <button
            type="button"
            role="tab"
            aria-selected={selectedBucket === null}
            onClick={() => onSelectBucket(null)}
            className={`inline-flex min-h-10 items-center rounded-[8px] px-3 text-left text-sm font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.96] motion-reduce:transform-none ${
              selectedBucket === null
                ? "bg-accent text-accent-contrast"
                : "text-text-muted hover:bg-card-hover hover:text-text-primary"
            }`}
          >
            <span>全部候選</span>
            <span
              className={`ml-2 rounded-full px-2 py-0.5 text-xs ${
                selectedBucket === null ? "bg-black/10 text-current" : "bg-badge-neutral-bg text-badge-neutral-text"
              }`}
            >
              {totalCount}
            </span>
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
                className={`inline-flex min-h-10 items-center rounded-[8px] px-3 text-left text-sm font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.96] motion-reduce:transform-none ${
                  active
                    ? "bg-accent text-accent-contrast"
                    : "text-text-muted hover:bg-card-hover hover:text-text-primary"
                }`}
              >
                <span>{BUCKET_LABEL[bucket]}</span>
                <span
                  className={`ml-2 rounded-full px-2 py-0.5 text-xs ${
                    active ? "bg-black/10 text-current" : "bg-badge-neutral-bg text-badge-neutral-text"
                  }`}
                >
                  {counts[bucket]}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function StaleRunDataNotice({ runDate, freshnessSummary }: { runDate: string; freshnessSummary: string }) {
  return (
    <section className="rounded-[14px] border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950">
      <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">資料日期落後掃描日</p>
      <p className="mt-2 text-sm leading-relaxed text-amber-800 dark:text-amber-300">
        本批次掃描日為 {formatDate(runDate)}，但部分資料日期較晚同步或仍落後；{freshnessSummary}
        結果應作為觀察追蹤與風險脈絡，不代表當前高信心排序。
      </p>
    </section>
  );
}

export function RunSummary({ run }: { run: DailyRadarRunResponse }) {
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
      <section className="overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel">
        <div className="flex flex-col gap-2 border-b border-border-subtle px-4 py-4 sm:flex-row sm:items-center sm:justify-between md:px-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-faint">雷達狀態</p>
            <h2 className="mt-1 text-base font-semibold text-text-primary">今日掃描狀態</h2>
          </div>
          <span className="ui-badge self-start sm:self-auto">{freshnessSummary}</span>
        </div>

        <div className="grid grid-cols-2 [&>*:nth-child(-n+2)]:border-t-0 [&>*:nth-child(odd)]:border-r [&>*:nth-child(odd)]:border-border-subtle lg:grid-cols-4 lg:[&>*:nth-child(odd)]:border-r-0">
          <RunMetric label="最新掃描日" value={formatDate(run.run_date)} helper="盤後觀察批次" />
          <RunMetric
            label="掃描狀態"
            value={formatRunStatusLabel(run.status)}
            helper={formatRunStatusHelper(run.status)}
            valueClass={getRunStatusClass(run.status)}
          />
          <RunMetric label="觀察候選數" value={String(run.candidates.length)} helper="符合規則的追蹤名單" />
          <RunMetric
            label="資料新鮮度"
            value={dataDateEntries.length > 0 ? "已同步" : "待確認"}
            helper={`${dataDateEntries.length} 項資料來源`}
          />
        </div>

        <details className="border-t border-border-subtle bg-surface px-4 py-3 md:px-5">
          <summary className="flex min-h-10 cursor-pointer items-center justify-between gap-3 text-sm font-medium text-text-secondary">
            <span>查看 {dataDateEntries.length} 項資料日期</span>
            <span className="text-right text-xs font-normal text-text-faint">{freshnessSummary}</span>
          </summary>
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            {dataDateEntries.length > 0 ? (
              dataDateEntries.map(([source, date]) => (
                <div key={source} className="rounded-[10px] border border-border-subtle bg-surface-raised px-3 py-2">
                  <p className="text-xs font-medium text-text-muted">{formatDataSourceLabel(source)}</p>
                  <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(date)}</p>
                </div>
              ))
            ) : (
              <p className="rounded-[10px] border border-border-subtle bg-surface-raised px-3 py-2 text-sm text-text-faint md:col-span-3">
                尚未收到資料日期，請稍後重新讀取觀察資料。
              </p>
            )}
          </div>
        </details>
      </section>

      {shouldShowStaleNotice && <StaleRunDataNotice runDate={run.run_date} freshnessSummary={freshnessSummary} />}

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
