import { useLatestDailyRadarQuery } from "../features/daily-radar/queries";
import { toDailyRadarDisplayError } from "../lib/dailyRadarApi";
import { RunSummary } from "../components/daily-radar/RunSummary";
import { LoadingState, ErrorState, WholeRunEmptyState } from "../components/daily-radar/QueryStates";

export default function DailyRadarPage() {
  const { data: run, isPending: loading, isFetching, error: queryError, refetch } = useLatestDailyRadarQuery();
  const error = queryError ? toDailyRadarDisplayError(queryError) : null;
  const refresh = () => {
    void refetch();
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
    </div>
  );
}
