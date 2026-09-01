import {
  ACTIVE_ETF_ACTION_CLASS as ACTION_CLASS,
  ACTIVE_ETF_ACTION_LABEL as ACTION_LABEL,
  getActiveEtfConsensusPresentation,
} from "../../features/active-etf/presentation";
import type { ActiveEtfChange, ActiveEtfConsensus } from "../../lib/activeEtfTypes";
import { DetailDrawer } from "../app-shell/DetailDrawer";

function formatShares(value: number): string {
  return new Intl.NumberFormat("zh-TW").format(value);
}

function formatSigned(value: number, digits = 2): string {
  return new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: "always",
  }).format(value);
}

export function ActiveEtfConsensusDrawer({
  consensus,
  changes,
  onClose,
}: {
  consensus: ActiveEtfConsensus;
  changes: ActiveEtfChange[];
  onClose: () => void;
}) {
  const direction = getActiveEtfConsensusPresentation(consensus);
  const comparisonRanges = new Set(changes.map((change) => `${change.previous_date} → ${change.data_date}`));
  const comparison = comparisonRanges.size === 1 ? [...comparisonRanges][0] : "比較區間依基金而異";

  return (
    <DetailDrawer
      eyebrow="個股 ETF 變化明細"
      title={`${consensus.symbol} ${consensus.name}`}
      description={`${comparison} · ${changes.length} 檔基金`}
      closeLabel="關閉個股 ETF 變化明細"
      onClose={onClose}
    >
      <section className="rounded-[10px] border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">本日變化分布</h3>
            <p className="mt-1 text-xs leading-relaxed text-text-muted">
              新增 {consensus.added_count} · 增加 {consensus.increased_count} · 減少 {consensus.decreased_count} · 移除{" "}
              {consensus.removed_count}
            </p>
          </div>
          <span className={`ui-badge ${direction.badgeClassName}`}>{direction.label}</span>
        </div>
        <p className="mt-3 border-t border-border-subtle pt-3 text-xs leading-relaxed text-text-faint">
          前後兩期皆通過雙來源驗證者會個別標註；未標註者為單一來源比較。
        </p>
      </section>

      <section aria-label="基金變化清單">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold text-text-primary">基金變化</h3>
          <span className="font-mono text-xs tabular-nums text-text-faint">{changes.length} 檔</span>
        </div>
        <div className="mt-3 overflow-hidden rounded-[10px] border border-border bg-surface-raised">
          {changes.map((change) => (
            <article
              key={`${change.fund_code}-${change.action}`}
              className="border-b border-border-subtle p-4 last:border-b-0"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-mono text-sm font-semibold text-text-primary">{change.fund_code}</p>
                  <p className="mt-1 text-sm leading-relaxed text-text-muted">{change.fund_name}</p>
                  <p className="mt-1 font-mono text-xs tabular-nums text-text-faint">
                    {change.previous_date} → {change.data_date}
                  </p>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <span className={`ui-badge ${ACTION_CLASS[change.action]}`}>{ACTION_LABEL[change.action]}</span>
                  {change.verification_status === "verified" && (
                    <span className="ui-badge bg-positive/12 text-positive">雙來源確認</span>
                  )}
                </div>
              </div>
              <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border-subtle pt-3 sm:grid-cols-4">
                <div>
                  <dt className="text-[0.6875rem] text-text-faint">前次持股</dt>
                  <dd className="mt-1 text-sm tabular-nums text-text-primary">
                    {formatShares(change.previous_shares)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[0.6875rem] text-text-faint">本次持股</dt>
                  <dd className="mt-1 text-sm tabular-nums text-text-primary">{formatShares(change.current_shares)}</dd>
                </div>
                <div>
                  <dt className="text-[0.6875rem] text-text-faint">股數增減</dt>
                  <dd
                    className={`mt-1 text-sm font-medium tabular-nums ${change.share_delta > 0 ? "text-positive" : "text-negative"}`}
                  >
                    {formatSigned(change.share_delta, 0)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[0.6875rem] text-text-faint">權重差</dt>
                  <dd className="mt-1 text-sm tabular-nums text-text-primary">
                    {formatSigned(change.weight_delta_pct_points)} pp
                  </dd>
                </div>
              </dl>
              {change.likely_fund_scale_change && (
                <p className="mt-3 text-xs leading-relaxed text-signal">可能包含基金申贖造成的等比例調整。</p>
              )}
            </article>
          ))}
        </div>
      </section>

      <p className="text-xs leading-relaxed text-text-faint">
        本頁呈現公開持股資料的日期間差異，不代表即時成交，也不構成買賣建議。
      </p>
    </DetailDrawer>
  );
}
