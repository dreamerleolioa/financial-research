import { useState } from "react";
import { useDailyRadarValidationQuery, useLatestDailyRadarQuery } from "../features/daily-radar/queries";
import { toDailyRadarDisplayError } from "../lib/dailyRadarApi";
import { RunSummary } from "../components/daily-radar/RunSummary";
import { ValidationResults } from "../components/daily-radar/ValidationResults";
import { LoadingState, ErrorState, WholeRunEmptyState } from "../components/daily-radar/QueryStates";

export default function DailyRadarPage() {
  const [view, setView] = useState<"observations" | "validation">("observations");
  const [lookbackDays, setLookbackDays] = useState(90);
  const {
    data: run,
    isPending: loading,
    isFetching: runFetching,
    error: queryError,
    refetch,
  } = useLatestDailyRadarQuery();
  const validation = useDailyRadarValidationQuery(view === "validation", lookbackDays);
  const isFetching = view === "validation" ? validation.isFetching : runFetching;
  const error = queryError ? toDailyRadarDisplayError(queryError) : null;
  const refresh = () => {
    if (view === "validation") void validation.refetch();
    else void refetch();
  };

  return (
    <div className="space-y-5">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">After-hours radar</p>
        <div className="mt-2 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-[-0.02em] text-text-primary md:text-3xl">盤後觀察雷達</h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-muted">
              將每日量價與籌碼訊號整理成可比較的觀察清單，快速確認追蹤狀態、主要分類與風險。
            </p>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={isFetching}
            className="ui-button-secondary self-start md:self-auto"
          >
            {isFetching ? "讀取中…" : "重新整理"}
          </button>
        </div>
      </header>

      <div
        role="tablist"
        aria-label="雷達查看方式"
        className="flex gap-1 rounded-[12px] border border-border bg-surface-raised p-1.5 shadow-panel"
      >
        {(["observations", "validation"] as const).map((tab) => (
          <button
            type="button"
            key={tab}
            role="tab"
            id={`radar-tab-${tab}`}
            aria-selected={view === tab}
            aria-controls={`radar-panel-${tab}`}
            tabIndex={view === tab ? 0 : -1}
            onClick={() => setView(tab)}
            onKeyDown={(e) => {
              if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
                e.preventDefault();
                const next =
                  e.key === "Home"
                    ? "observations"
                    : e.key === "End"
                      ? "validation"
                      : tab === "observations"
                        ? "validation"
                        : "observations";
                setView(next);
                document.getElementById(`radar-tab-${next}`)?.focus();
              }
            }}
            className={`min-h-10 rounded-[8px] px-4 text-sm font-medium transition-colors duration-150 ${view === tab ? "bg-accent text-accent-contrast" : "text-text-muted hover:bg-card-hover hover:text-text-primary"}`}
          >
            {tab === "observations" ? "觀察名單" : "驗證結果"}
          </button>
        ))}
      </div>
      <section
        role="tabpanel"
        id="radar-panel-observations"
        aria-labelledby="radar-tab-observations"
        hidden={view !== "observations"}
        className="space-y-5"
      >
        {error && (
          <div role="alert" className="space-y-2">
            <ErrorState error={error} onRetry={refresh} />
            {run && <p className="text-sm text-text-muted">更新失敗，以下保留上次成功讀取的資料。</p>}
          </div>
        )}
        {loading ? (
          <LoadingState />
        ) : run ? (
          <RunSummary run={run} />
        ) : !error ? (
          <WholeRunEmptyState onRefresh={refresh} />
        ) : null}
      </section>
      <section
        role="tabpanel"
        id="radar-panel-validation"
        aria-labelledby="radar-tab-validation"
        hidden={view !== "validation"}
      >
        <ValidationResults
          lookbackDays={lookbackDays}
          onLookbackChange={setLookbackDays}
          data={validation.data}
          loading={validation.isPending}
          error={validation.error}
          onRetry={() => {
            void validation.refetch();
          }}
        />
      </section>
    </div>
  );
}
