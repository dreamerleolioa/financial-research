import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ActiveEtfChangeDrawer } from "../components/active-etf/ActiveEtfChangeDrawer";
import { WorkspaceEmptyState } from "../components/app-shell/WorkspaceEmptyState";
import { useActiveEtfDailyQuery } from "../features/active-etf/queries";
import { ApiError } from "../lib/apiClient";
import type {
  ActiveEtfAction,
  ActiveEtfChange,
  ActiveEtfConsensus,
  ActiveEtfCoverageFund,
} from "../lib/activeEtfTypes";

const ACTION_LABEL: Record<ActiveEtfAction, string> = {
  added: "新增持股",
  increased: "持股增加",
  decreased: "持股減少",
  removed: "不再持有",
};

const ACTION_CLASS: Record<ActiveEtfAction, string> = {
  added: "bg-signal/15 text-signal",
  increased: "bg-positive/12 text-positive",
  decreased: "bg-negative/12 text-negative",
  removed: "bg-badge-neutral-bg text-badge-neutral-text",
};

type View = "funds" | "consensus";
type ActionFilter = "all" | ActiveEtfAction;
const CHANGE_PAGE_SIZE = 100;

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

function formatTimestamp(value: string | null): string {
  if (!value) return "尚未取得";
  return new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatSource(provider: string): string {
  if (provider.toLowerCase() === "moneydj") return "MoneyDJ";
  if (provider.toLowerCase() === "issuer_official") return "發行投信官方資料";
  return provider;
}

function verificationReason(fund: ActiveEtfCoverageFund): string {
  const reason = fund.verification_reason;
  if (reason === "official_source_unsupported") return "尚未接上完整的發行投信官方持股來源";
  if (reason === "verification_source_unavailable") return "發行投信官方來源本次擷取失敗";
  if (reason === "source_date_mismatch") return "兩個來源的資料日期不同";
  if (reason === "holding_mismatch") return "兩個來源的持股代碼或股數不一致";
  if (reason === "share_inventory_match") return "兩個來源的持股代碼與股數逐筆一致";
  if (fund.status === "missing") return "當日尚未取得持股資料";
  return "來源驗證狀態尚未完整回報";
}

function CoverageMetric({ label, value, helper }: { label: string; value: string; helper: string }) {
  return (
    <div className="min-w-0 border-l-2 border-border-subtle pl-3">
      <dt className="text-xs text-text-faint">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums text-text-primary">{value}</dd>
      <p className="mt-1 text-xs text-text-muted">{helper}</p>
    </div>
  );
}

function VerificationBadge({ fund }: { fund: ActiveEtfCoverageFund }) {
  const copy =
    fund.verification_status === "verified"
      ? { label: "已雙來源確認", className: "bg-positive/12 text-positive" }
      : fund.verification_status === "conflict"
        ? { label: "來源不一致", className: "bg-negative/12 text-negative" }
        : fund.status === "missing"
          ? { label: "當日未更新", className: "bg-badge-neutral-bg text-badge-neutral-text" }
          : { label: "單一來源", className: "bg-badge-neutral-bg text-badge-neutral-text" };
  return <span className={`ui-badge ${copy.className}`}>{copy.label}</span>;
}

function ChangeVerificationBadge({ change }: { change: ActiveEtfChange }) {
  if (change.verification_status !== "verified") return null;
  return <span className="ui-badge bg-positive/12 text-positive">雙來源確認</span>;
}

function FundEvidenceSummary({ fund }: { fund: ActiveEtfCoverageFund }) {
  const comparisonState =
    fund.status === "ready"
      ? `可比較 ${fund.previous_date} → ${fund.data_date}`
      : fund.status === "no_baseline"
        ? "已有可用快照，尚無前次資料可比較"
        : verificationReason(fund);
  const sourceNote = fund.verification_status === "single_source" ? verificationReason(fund) : null;

  return (
    <section className="mb-4 rounded-[12px] border border-border bg-surface-raised p-4 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-sm font-semibold text-text-primary">
            {fund.fund_code} <span className="font-sans font-medium">{fund.name}</span>
          </p>
          <p className="mt-1 text-xs leading-relaxed text-text-muted">{comparisonState}</p>
          {sourceNote && <p className="mt-1 text-xs leading-relaxed text-text-faint">{sourceNote}</p>}
        </div>
        <VerificationBadge fund={fund} />
      </div>
      {fund.sources.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {fund.sources.map((source) => (
            <a
              key={`${source.source_provider}-${source.data_date}`}
              href={source.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-[10px] border border-border-subtle bg-card px-3 py-2 text-xs text-text-muted transition-colors hover:bg-card-hover hover:text-text-primary"
            >
              <span className="flex items-center justify-between gap-2">
                <strong className="font-medium text-text-primary">{formatSource(source.source_provider)}</strong>
                <span aria-hidden="true">↗</span>
              </span>
              <span className="mt-1 block tabular-nums text-text-faint">
                資料日 {source.data_date} · {source.payload_hash.slice(0, 8)}
              </span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

function FundIndex({
  funds,
  selectedFund,
  onSelect,
}: {
  funds: ActiveEtfCoverageFund[];
  selectedFund: string;
  onSelect: (fundCode: string) => void;
}) {
  const totalChanges = funds.reduce((total, fund) => total + fund.change_count, 0);
  return (
    <aside className="hidden xl:block">
      <div className="sticky top-8 overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel">
        <div className="border-b border-border-subtle px-4 py-3">
          <h3 className="text-sm font-semibold text-text-primary">基金索引</h3>
          <p className="mt-1 text-xs text-text-muted">選擇一檔查看當日差異</p>
        </div>
        <div className="max-h-[calc(100dvh-12rem)] overflow-y-auto p-2">
          <button
            type="button"
            onClick={() => onSelect("all")}
            aria-pressed={selectedFund === "all"}
            className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-[10px] px-3 text-left text-sm transition-colors ${
              selectedFund === "all" ? "bg-accent-soft text-text-primary" : "text-text-muted hover:bg-card-hover"
            }`}
          >
            <span className="font-medium">全部基金</span>
            <span className="tabular-nums text-text-faint">{totalChanges}</span>
          </button>
          {funds.map((fund) => (
            <button
              key={fund.fund_code}
              type="button"
              onClick={() => onSelect(fund.fund_code)}
              aria-pressed={selectedFund === fund.fund_code}
              className={`mt-1 w-full rounded-[10px] px-3 py-2.5 text-left transition-colors ${
                selectedFund === fund.fund_code ? "bg-accent-soft" : "hover:bg-card-hover"
              }`}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs font-semibold text-text-primary">{fund.fund_code}</span>
                <span className="text-xs tabular-nums text-text-faint">{fund.change_count}</span>
              </span>
              <span className="mt-1 block truncate text-xs text-text-muted">{fund.name}</span>
              <span className="mt-1.5 block">
                <VerificationBadge fund={fund} />
              </span>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}

function ChangeTable({
  changes,
  onSelect,
}: {
  changes: ActiveEtfChange[];
  onSelect: (change: ActiveEtfChange) => void;
}) {
  if (changes.length === 0) {
    return (
      <WorkspaceEmptyState
        eyebrow="No changes"
        title="目前篩選條件沒有持股變化"
        description="可切換基金、變化類型或清除搜尋條件。沒有差異也可能代表基金持股尚未更新。"
      />
    );
  }

  return (
    <>
      <div className="hidden overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel xl:block">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-sm">
            <thead className="bg-shell text-left text-xs text-text-faint">
              <tr>
                <th scope="col" className="px-3 py-3 font-medium">
                  標的
                </th>
                <th scope="col" className="px-3 py-3 font-medium">
                  基金
                </th>
                <th scope="col" className="px-3 py-3 font-medium">
                  變化與來源
                </th>
                <th scope="col" className="px-3 py-3 text-right font-medium">
                  持股股數
                </th>
                <th scope="col" className="px-3 py-3 text-right font-medium">
                  股數增減
                </th>
                <th scope="col" className="px-3 py-3 text-right font-medium">
                  權重差
                </th>
                <th scope="col" className="px-3 py-3 text-right font-medium">
                  <span className="sr-only">操作</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {changes.map((change) => (
                <tr
                  key={`${change.fund_code}-${change.symbol}-${change.action}`}
                  className="border-t border-border-subtle hover:bg-card-hover/60"
                >
                  <td className="px-3 py-3">
                    <p className="font-mono font-semibold text-text-primary">{change.symbol}</p>
                    <p className="mt-0.5 text-xs text-text-muted">{change.name}</p>
                  </td>
                  <td className="px-3 py-3">
                    <p className="font-mono text-xs font-semibold text-text-primary">{change.fund_code}</p>
                    <p className="mt-0.5 max-w-40 truncate text-xs text-text-muted">{change.fund_name}</p>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-col items-start gap-1.5">
                      <span className={`ui-badge ${ACTION_CLASS[change.action]}`}>{ACTION_LABEL[change.action]}</span>
                      <ChangeVerificationBadge change={change} />
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right tabular-nums text-text-secondary">
                    {formatShares(change.current_shares)}
                  </td>
                  <td
                    className={`px-3 py-3 text-right font-medium tabular-nums ${change.share_delta > 0 ? "text-positive" : "text-negative"}`}
                  >
                    {formatSigned(change.share_delta, 0)}
                  </td>
                  <td className="px-3 py-3 text-right tabular-nums text-text-secondary">
                    {formatSigned(change.weight_delta_pct_points)} pp
                  </td>
                  <td className="px-3 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => onSelect(change)}
                      className="ui-button-secondary min-h-9 px-3 text-xs"
                      aria-label={`查看 ${change.symbol} ${change.name} 持股變化`}
                    >
                      查看
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid gap-3 xl:hidden">
        {changes.map((change) => (
          <article
            key={`${change.fund_code}-${change.symbol}-${change.action}`}
            className="rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-mono text-sm font-semibold text-text-primary">{change.symbol}</p>
                <h3 className="mt-1 truncate font-medium text-text-primary">{change.name}</h3>
                <p className="mt-1 truncate text-xs text-text-muted">
                  {change.fund_code} {change.fund_name}
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1.5">
                <span className={`ui-badge ${ACTION_CLASS[change.action]}`}>{ACTION_LABEL[change.action]}</span>
                <ChangeVerificationBadge change={change} />
              </div>
            </div>
            <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-border-subtle pt-3">
              <div>
                <dt className="text-[0.6875rem] text-text-faint">本次股數</dt>
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
            <button
              type="button"
              onClick={() => onSelect(change)}
              className="ui-button-secondary mt-4 w-full"
              aria-label={`查看 ${change.symbol} ${change.name} 持股變化`}
            >
              查看變化明細
            </button>
          </article>
        ))}
      </div>
    </>
  );
}

function ConsensusList({ consensus }: { consensus: ActiveEtfConsensus[] }) {
  if (consensus.length === 0) {
    return (
      <WorkspaceEmptyState
        eyebrow="Consensus"
        title="目前沒有個股持股變化"
        description="任一基金出現持股差異就會列入；多檔基金同時出現時會另外標註。"
      />
    );
  }
  return (
    <div className="overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel">
      {consensus.map((item, index) => {
        const hasFundConsensus = item.fund_count >= 2;
        const directionLabel =
          item.direction === "increase"
            ? hasFundConsensus
              ? "共同增加"
              : "單一基金增加"
            : item.direction === "decrease"
              ? hasFundConsensus
                ? "共同減少"
                : "單一基金減少"
              : "方向分歧";
        const directionClass =
          item.direction === "increase"
            ? "text-positive"
            : item.direction === "decrease"
              ? "text-negative"
              : "text-signal";
        return (
          <article
            key={item.symbol}
            className="ui-data-row grid gap-3 sm:grid-cols-[2.5rem_minmax(0,1fr)_auto] sm:items-center"
          >
            <span className="font-mono text-sm tabular-nums text-text-faint">{String(index + 1).padStart(2, "0")}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h3 className="font-mono font-semibold text-text-primary">{item.symbol}</h3>
                <p className="text-sm text-text-muted">{item.name}</p>
              </div>
              <p className="mt-1 text-xs text-text-faint">
                新增 {item.added_count} · 增加 {item.increased_count} · 減少 {item.decreased_count} · 移除{" "}
                {item.removed_count}
              </p>
            </div>
            <div className="flex items-center justify-between gap-4 sm:justify-end">
              <span className={`text-sm font-medium ${directionClass}`}>{directionLabel}</span>
              {hasFundConsensus ? (
                <span className="ui-badge bg-positive/12 text-positive">{item.fund_count} 檔共識</span>
              ) : (
                <span className="min-w-16 text-right font-mono text-sm tabular-nums text-text-muted">1 檔基金</span>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}

export default function ActiveEtfPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedDate = searchParams.get("date") ?? undefined;
  const query = useActiveEtfDailyQuery(requestedDate);
  const [view, setView] = useState<View>("funds");
  const [selectedFund, setSelectedFund] = useState("all");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("all");
  const [search, setSearch] = useState("");
  const [selectedChange, setSelectedChange] = useState<ActiveEtfChange | null>(null);
  const [visibleChangeCount, setVisibleChangeCount] = useState(CHANGE_PAGE_SIZE);
  const closeChangeDrawer = useCallback(() => setSelectedChange(null), []);

  useEffect(() => {
    if (selectedFund === "all" || !query.data) return;
    if (!query.data.funds.some((fund) => fund.fund_code === selectedFund)) setSelectedFund("all");
  }, [query.data, selectedFund]);

  useEffect(() => {
    setVisibleChangeCount(CHANGE_PAGE_SIZE);
  }, [actionFilter, query.data?.data_date, search, selectedFund]);

  const scopedChanges = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase("zh-TW");
    return (query.data?.changes ?? []).filter((change) => {
      if (selectedFund !== "all" && change.fund_code !== selectedFund) return false;
      if (!normalizedSearch) return true;
      return [change.symbol, change.name, change.fund_code, change.fund_name].some((value) =>
        value.toLocaleLowerCase("zh-TW").includes(normalizedSearch),
      );
    });
  }, [query.data?.changes, search, selectedFund]);

  const actionCounts = useMemo(
    () =>
      scopedChanges.reduce(
        (counts, change) => {
          counts[change.action] += 1;
          return counts;
        },
        { added: 0, increased: 0, decreased: 0, removed: 0 } as Record<ActiveEtfAction, number>,
      ),
    [scopedChanges],
  );
  const filteredChanges = useMemo(
    () => scopedChanges.filter((change) => actionFilter === "all" || change.action === actionFilter),
    [actionFilter, scopedChanges],
  );
  const visibleChanges = filteredChanges.slice(0, visibleChangeCount);

  if (query.isPending) {
    return (
      <section className="ui-panel flex min-h-56 items-center justify-center p-6 text-center">
        <div>
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
          <p className="mt-4 text-sm font-medium">正在讀取 ETF 持股變化</p>
          <p className="mt-1 text-xs text-text-muted">完成後會顯示各基金資料日與比較狀態。</p>
        </div>
      </section>
    );
  }

  if (query.isError) {
    const noData = query.error instanceof ApiError && query.error.status === 404;
    return (
      <WorkspaceEmptyState
        eyebrow={noData ? "No snapshot" : "Load failed"}
        title={noData ? "尚無主動式 ETF 持股快照" : "持股變化暫時無法載入"}
        description={
          noData
            ? "每日擷取完成並累積兩個資料日後，這裡才會開始顯示可比較的持股增減。"
            : "請稍後再試；若持續發生，需確認每日擷取流程與來源狀態。"
        }
        actions={
          <button type="button" onClick={() => void query.refetch()} className="ui-button-secondary">
            重新讀取
          </button>
        }
      />
    );
  }

  const data = query.data;
  const latestFetchedAt = data.funds.reduce<string | null>((latest, fund) => {
    if (!fund.fetched_at) return latest;
    return latest == null || fund.fetched_at > latest ? fund.fetched_at : latest;
  }, null);
  const qualityCounts = data.funds.reduce(
    (counts, fund) => {
      if (fund.verification_status === "verified") counts.verified += 1;
      else if (fund.verification_status === "conflict") counts.conflict += 1;
      else if (fund.verification_status === "single_source") counts.singleSource += 1;
      else if (fund.status === "missing") counts.missing += 1;
      return counts;
    },
    { verified: 0, singleSource: 0, conflict: 0, missing: 0 },
  );
  const allVerified = qualityCounts.verified === data.expected_funds;
  const hasCoverageGap = qualityCounts.conflict > 0 || qualityCounts.missing > 0;
  const coverageNoticeClass = allVerified
    ? "border-positive/25 bg-positive/8 text-text-muted"
    : hasCoverageGap
      ? "border-signal/30 bg-signal/10 text-text-muted"
      : "border-border bg-card text-text-muted";
  const selectedFundRecord = data.funds.find((fund) => fund.fund_code === selectedFund) ?? null;
  const selectedChangeFund = selectedChange
    ? (data.funds.find((fund) => fund.fund_code === selectedChange.fund_code) ?? null)
    : null;

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-3xl">
          <p className="text-[0.6875rem] font-semibold tracking-[0.14em] text-accent uppercase">Active ETF holdings</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">
            主動式 ETF 持股追蹤
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-text-muted">
            比較各基金連續公開資料日的持股股數與權重差異，並逐檔揭露來源驗證結果與資料缺口。
          </p>
        </div>
        <label className="w-full sm:w-48">
          <span className="mb-1.5 block text-xs font-medium text-text-muted">資料日</span>
          <select
            className="ui-input"
            value={data.data_date}
            onChange={(event) => setSearchParams({ date: event.target.value })}
            aria-label="選擇 ETF 持股資料日"
          >
            {data.available_dates.map((date) => (
              <option key={date} value={date}>
                {date}
              </option>
            ))}
          </select>
        </label>
      </header>

      <section
        className="rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel sm:p-5"
        aria-labelledby="coverage-title"
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold tracking-wide text-text-faint uppercase">資料覆蓋</p>
            <h3 id="coverage-title" className="mt-1 font-semibold text-text-primary">
              {data.data_date} 公開持股比較
            </h3>
          </div>
          <span
            className={`ui-badge self-start sm:self-auto ${allVerified ? "bg-positive/12 text-positive" : hasCoverageGap ? "bg-signal/15 text-signal" : "bg-badge-neutral-bg text-badge-neutral-text"}`}
          >
            {allVerified ? "全部通過雙來源" : hasCoverageGap ? "有來源缺口" : "含單一來源資料"}
          </span>
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-5 lg:grid-cols-4">
          <CoverageMetric label="追蹤基金" value={String(data.expected_funds)} helper="當日預期檢查總數" />
          <CoverageMetric
            label="雙來源確認"
            value={`${qualityCounts.verified} / ${data.expected_funds}`}
            helper="代碼與股數逐筆一致"
          />
          <CoverageMetric label="單一來源" value={String(qualityCounts.singleSource)} helper="照常發布，雙來源另標註" />
          <CoverageMetric label="來源不一致" value={String(qualityCounts.conflict)} helper="停止比較，等待查核" />
        </dl>
        <div className={`mt-4 rounded-[10px] border px-3 py-2 text-xs leading-relaxed ${coverageNoticeClass}`}>
          <p>單一來源且已有前次快照也會發布變化；前後兩期都經雙來源確認時，會另外標註。</p>
          <p className="mt-1 tabular-nums text-text-faint">
            本次發布 {data.summary.changed_funds} 檔基金、{data.summary.changed_rows} 筆變化 · 來源更新{" "}
            {formatTimestamp(latestFetchedAt)}
            {qualityCounts.missing > 0 ? ` · 當日未更新 ${qualityCounts.missing} 檔` : ""}
          </p>
        </div>
      </section>

      <section>
        <div className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div
            className="inline-flex w-fit rounded-[10px] border border-border bg-shell p-1"
            role="group"
            aria-label="ETF 持股檢視"
          >
            <button
              type="button"
              onClick={() => setView("funds")}
              aria-pressed={view === "funds"}
              className={`min-h-10 rounded-md px-4 text-sm font-medium ${view === "funds" ? "bg-surface-raised text-accent shadow-panel" : "text-text-muted"}`}
            >
              基金變化
            </button>
            <button
              type="button"
              onClick={() => setView("consensus")}
              aria-pressed={view === "consensus"}
              className={`min-h-10 rounded-md px-4 text-sm font-medium ${view === "consensus" ? "bg-surface-raised text-accent shadow-panel" : "text-text-muted"}`}
            >
              個股共識
            </button>
          </div>

          {view === "funds" && (
            <div className="grid gap-3 sm:grid-cols-2 lg:flex lg:items-end">
              <label className="lg:w-52 xl:hidden">
                <span className="mb-1.5 block text-xs font-medium text-text-muted">基金</span>
                <select
                  className="ui-input"
                  value={selectedFund}
                  onChange={(event) => setSelectedFund(event.target.value)}
                >
                  <option value="all">全部基金</option>
                  {data.funds.map((fund) => (
                    <option key={fund.fund_code} value={fund.fund_code}>
                      {fund.fund_code} {fund.name}
                      {fund.verification_status === "verified"
                        ? "（已確認）"
                        : fund.verification_status === "conflict"
                          ? "（來源不一致）"
                          : fund.status === "missing"
                            ? "（未更新）"
                            : "（單一來源）"}
                    </option>
                  ))}
                </select>
              </label>
              <label className="lg:w-64">
                <span className="mb-1.5 block text-xs font-medium text-text-muted">搜尋標的或基金</span>
                <input
                  className="ui-input"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="代號、名稱或基金"
                  type="search"
                />
              </label>
            </div>
          )}
        </div>

        {view === "funds" ? (
          <>
            <div className="mt-4 flex gap-2 overflow-x-auto pb-1" aria-label="持股變化類型">
              {(["all", "added", "increased", "decreased", "removed"] as const).map((action) => (
                <button
                  key={action}
                  type="button"
                  onClick={() => setActionFilter(action)}
                  aria-pressed={actionFilter === action}
                  className={`min-h-9 shrink-0 rounded-full border px-3 text-xs font-medium transition-colors ${actionFilter === action ? "border-accent bg-accent-soft text-accent" : "border-border bg-surface-raised text-text-muted hover:bg-card-hover"}`}
                >
                  {action === "all"
                    ? `全部 ${scopedChanges.length}`
                    : `${ACTION_LABEL[action]} ${actionCounts[action]}`}
                </button>
              ))}
            </div>
            <div className="mt-4 grid gap-5 xl:grid-cols-[15rem_minmax(0,1fr)]">
              <FundIndex funds={data.funds} selectedFund={selectedFund} onSelect={setSelectedFund} />
              <div className="min-w-0">
                {selectedFundRecord && <FundEvidenceSummary fund={selectedFundRecord} />}
                <ChangeTable changes={visibleChanges} onSelect={setSelectedChange} />
                {filteredChanges.length > 0 && (
                  <div className="mt-4 flex flex-col items-center gap-2 text-xs text-text-faint sm:flex-row sm:justify-between">
                    <p className="tabular-nums">
                      已顯示 {visibleChanges.length} / {filteredChanges.length} 筆
                    </p>
                    {visibleChanges.length < filteredChanges.length && (
                      <button
                        type="button"
                        onClick={() => setVisibleChangeCount((count) => count + CHANGE_PAGE_SIZE)}
                        className="ui-button-secondary w-full sm:w-auto"
                      >
                        顯示更多
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="mt-4">
            <div className="mb-3">
              <h3 className="text-sm font-semibold text-text-primary">個股變化彙整</h3>
              <p className="mt-1 text-xs leading-relaxed text-text-muted">
                單檔基金的變化也會列出；兩檔以上方向一致時，才加上多基金共識標記。
              </p>
            </div>
            <ConsensusList consensus={data.consensus} />
          </div>
        )}
      </section>

      <section className="border-t border-border pt-4 text-xs leading-relaxed text-text-faint">
        <p>
          資料為公開持股頁面的每日快照比較。來源發布時間可能不同，股數變動也可能受到基金申贖影響，請搭配個別基金原始頁面判讀。
        </p>
      </section>

      {selectedChange && selectedChangeFund && (
        <ActiveEtfChangeDrawer change={selectedChange} fund={selectedChangeFund} onClose={closeChangeDrawer} />
      )}
    </div>
  );
}
