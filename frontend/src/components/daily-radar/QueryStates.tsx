import { Link } from "react-router-dom";
import { WorkspaceEmptyState } from "../app-shell/WorkspaceEmptyState";
import { type DailyRadarDisplayError } from "../../lib/dailyRadarApi";

export function LoadingState() {
  return (
    <section className="rounded-[14px] border border-border bg-surface-raised p-6 text-center shadow-panel">
      <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-border border-t-accent" />
      <p className="mt-4 text-sm font-medium text-text-primary">資料載入中</p>
      <p className="mt-1 text-xs text-text-muted">正在讀取最新盤後觀察雷達，完成後會顯示掃描日期與資料新鮮度。</p>
    </section>
  );
}

export function ErrorState({ error, onRetry }: { error: DailyRadarDisplayError; onRetry: () => void }) {
  return (
    <section className="rounded-xl border border-red-200 bg-red-50 p-6 shadow-sm dark:border-red-900 dark:bg-red-950">
      <p className="text-sm font-semibold text-red-700 dark:text-red-300">每日觀察雷達暫時無法載入</p>
      <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error.message}</p>
      {error.status && <p className="mt-1 text-xs text-red-600 dark:text-red-400">狀態碼：{error.status}</p>}
      <button onClick={onRetry} className="ui-button-primary mt-4">
        重新讀取觀察資料
      </button>
    </section>
  );
}

export function WholeRunEmptyState({ onRefresh }: { onRefresh?: () => void }) {
  return (
    <WorkspaceEmptyState
      eyebrow="Radar standing by"
      title="目前沒有可顯示的觀察候選"
      description="這可能代表本次規則掃描沒有候選通過門檻，或最新公開批次尚未完成。這不是交易結論，可稍後重新讀取資料。"
      actions={
        onRefresh ? (
          <button type="button" onClick={onRefresh} className="ui-button-primary">
            重新讀取雷達
          </button>
        ) : (
          <Link to="/analyze" className="ui-button-secondary">
            前往個股分析
          </Link>
        )
      }
    />
  );
}
