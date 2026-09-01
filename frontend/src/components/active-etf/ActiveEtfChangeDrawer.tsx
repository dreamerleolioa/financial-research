import type { ActiveEtfChange, ActiveEtfCoverageFund, ActiveEtfPeriodEvidence } from "../../lib/activeEtfTypes";
import { ACTIVE_ETF_ACTION_LABEL as ACTION_LABEL } from "../../features/active-etf/presentation";
import { DetailDrawer } from "../app-shell/DetailDrawer";

function formatShares(value: number): string {
  return new Intl.NumberFormat("zh-TW").format(value);
}

function formatSigned(value: number, digits = 2): string {
  const formatted = new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: "always",
  }).format(value);
  return formatted;
}

function formatSource(provider: string): string {
  if (provider.toLowerCase() === "moneydj") return "MoneyDJ";
  return provider;
}

function Metric({ label, value, helper }: { label: string; value: string; helper?: string }) {
  return (
    <div className="rounded-[10px] border border-border-subtle bg-card p-3">
      <dt className="text-xs text-text-faint">{label}</dt>
      <dd className="mt-1 font-semibold tabular-nums text-text-primary">{value}</dd>
      {helper && <p className="mt-1 text-xs leading-relaxed text-text-muted">{helper}</p>}
    </div>
  );
}

function PeriodEvidence({ evidence }: { evidence: ActiveEtfPeriodEvidence }) {
  const periodLabel = evidence.period === "current" ? "本期" : "前期";
  return (
    <section
      aria-label={`${periodLabel}證據 ${evidence.data_date}`}
      className="rounded-[10px] border border-border-subtle bg-surface-raised p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-medium text-text-primary">{periodLabel}資料</h4>
          <p className="mt-0.5 font-mono text-xs tabular-nums text-text-faint">{evidence.data_date}</p>
        </div>
        <span className="ui-badge bg-badge-neutral-bg text-badge-neutral-text">MoneyDJ</span>
      </div>
      <div className="mt-3 grid gap-2">
        {evidence.sources.map((source) => (
          <a
            key={`${source.source_provider}-${source.data_date}`}
            href={source.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex min-h-11 items-center justify-between gap-3 rounded-[10px] border border-border-subtle bg-card px-3 py-2 text-sm text-text-muted transition-colors hover:bg-card-hover hover:text-text-primary"
          >
            <span>
              <strong className="block font-medium text-text-primary">{formatSource(source.source_provider)}</strong>
              <span className="mt-0.5 block text-xs tabular-nums text-text-faint">
                資料日 {source.data_date} · 校驗碼 {source.payload_hash.slice(0, 8)}
              </span>
            </span>
            <span className="shrink-0" aria-hidden="true">
              ↗
            </span>
          </a>
        ))}
      </div>
    </section>
  );
}

export function ActiveEtfChangeDrawer({
  change,
  fund,
  onClose,
}: {
  change: ActiveEtfChange;
  fund: ActiveEtfCoverageFund;
  onClose: () => void;
}) {
  return (
    <DetailDrawer
      eyebrow="持股變化明細"
      title={`${change.symbol} ${change.name}`}
      description={`${change.fund_code} ${change.fund_name} · ${ACTION_LABEL[change.action]}`}
      closeLabel="關閉持股變化明細"
      onClose={onClose}
    >
      <section>
        <h3 className="text-sm font-semibold text-text-primary">股數變化</h3>
        <dl className="mt-3 grid grid-cols-2 gap-3">
          <Metric label="前次持股" value={formatShares(change.previous_shares)} />
          <Metric label="本次持股" value={formatShares(change.current_shares)} />
          <Metric label="股數增減" value={formatSigned(change.share_delta, 0)} />
          <Metric
            label="原始變化率"
            value={change.share_delta_pct == null ? "不適用" : `${formatSigned(change.share_delta_pct)}%`}
          />
        </dl>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-text-primary">權重與規模校正</h3>
        <dl className="mt-3 grid grid-cols-2 gap-3">
          <Metric label="前次權重" value={`${change.previous_weight_pct.toFixed(2)}%`} />
          <Metric label="本次權重" value={`${change.current_weight_pct.toFixed(2)}%`} />
          <Metric label="權重差" value={`${formatSigned(change.weight_delta_pct_points)} 個百分點`} />
          <Metric
            label="相對股數變化"
            value={
              change.relative_share_change_pct == null
                ? "無法估算"
                : `${formatSigned(change.relative_share_change_pct)}%`
            }
            helper="以同基金多數持股的共同變動比例校正。"
          />
        </dl>
      </section>

      {change.likely_fund_scale_change && (
        <section className="rounded-[10px] border border-signal/35 bg-signal/10 p-4">
          <h3 className="text-sm font-semibold text-text-primary">可能受基金規模變動影響</h3>
          <p className="mt-1 text-sm leading-relaxed text-text-muted">
            多數持股呈現相近比例變動，可能包含基金申贖造成的等比例調整，不宜直接解讀為經理人主動交易。
          </p>
        </section>
      )}

      <section className="rounded-[10px] border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-text-primary">比較與資料來源</h3>
          <span className="ui-badge bg-badge-neutral-bg text-badge-neutral-text">MoneyDJ</span>
        </div>
        <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-text-faint">比較區間</dt>
            <dd className="mt-1 tabular-nums text-text-primary">
              {change.previous_date} → {change.data_date}
            </dd>
          </div>
          <div>
            <dt className="text-text-faint">本期來源擷取時間</dt>
            <dd className="mt-1 tabular-nums text-text-primary">
              {new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short" }).format(
                new Date(change.fetched_at),
              )}
            </dd>
          </div>
        </dl>
        {fund.evidence_periods.length > 0 ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {fund.evidence_periods.map((evidence) => (
              <PeriodEvidence key={evidence.period} evidence={evidence} />
            ))}
          </div>
        ) : (
          <p className="mt-4 rounded-[10px] border border-border-subtle bg-surface-raised px-3 py-2 text-xs leading-relaxed text-text-muted">
            此 API 版本未提供前後期來源明細，無法顯示各期的原始資料連結。
          </p>
        )}
      </section>

      <p className="text-xs leading-relaxed text-text-faint">
        本頁僅呈現公開持股資料的日期間差異，不代表即時成交，也不構成買賣建議。
      </p>
    </DetailDrawer>
  );
}
