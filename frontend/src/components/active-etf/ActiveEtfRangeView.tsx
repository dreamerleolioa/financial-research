import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useActiveEtfRangeQuery } from "../../features/active-etf/queries";
import { ACTIVE_ETF_ACTION_LABEL } from "../../features/active-etf/presentation";
import type { ActiveEtfRangeResponse } from "../../lib/activeEtfSchemas";
import { WorkspaceEmptyState } from "../app-shell/WorkspaceEmptyState";

const number = (value: number | null, signed = false) =>
  value === null
    ? "無法比較"
    : new Intl.NumberFormat("zh-TW", {
        maximumFractionDigits: 2,
        signDisplay: signed ? "exceptZero" : "auto",
      }).format(value);
const shiftDate = (value: string, days: number) => {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
};

export function ActiveEtfRangeView() {
  const [params, setParams] = useSearchParams();
  const start = params.get("start") || undefined;
  const end = params.get("end") || undefined;
  const search = params.get("q") ?? "";
  const selected = params.get("stock") || undefined;
  const query = useActiveEtfRangeQuery(start, end);
  const data = query.data;
  useEffect(() => {
    if (!data || (start && end)) return;
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (!next.get("start")) next.set("start", data.start_date);
        if (!next.get("end")) next.set("end", data.end_date);
        return next;
      },
      { replace: true },
    );
  }, [data, start, end, setParams]);
  const [draftStart, setDraftStart] = useState(start ?? "");
  const [draftEnd, setDraftEnd] = useState(end ?? "");
  const [visibleCount, setVisibleCount] = useState(50);
  useEffect(() => {
    setDraftStart(start ?? data?.start_date ?? "");
    setDraftEnd(end ?? data?.end_date ?? "");
  }, [start, end, data?.start_date, data?.end_date]);
  useEffect(() => setVisibleCount(50), [start, end, search]);
  const update = (values: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => (value === null ? next.delete(key) : next.set(key, value)));
    setParams(next, { replace });
  };
  const stocks = (data?.stocks ?? []).filter((stock) =>
    [stock.symbol, stock.name].some((value) =>
      value.toLocaleLowerCase("zh-TW").includes(search.trim().toLocaleLowerCase("zh-TW")),
    ),
  );
  const selectedStock = stocks.find((stock) => stock.symbol === selected);
  const detailSection = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selectedStock) detailSection.current?.focus();
  }, [selectedStock?.symbol]);
  const detail = useActiveEtfRangeQuery(data?.start_date, data?.end_date, selected, Boolean(selectedStock));
  const invalidRange =
    !draftStart ||
    !draftEnd ||
    draftStart > draftEnd ||
    (Date.parse(draftEnd) - Date.parse(draftStart)) / 86400000 > 365;

  const openDaily = (date: string) =>
    update({
      mode: "daily",
      date,
      view: "consensus",
      q: selected ?? search,
      from: "range",
      range_q: search,
      start: data?.start_date ?? start ?? null,
      end: data?.end_date ?? end ?? null,
    });

  return (
    <div className="space-y-5">
      <header>
        <p className="text-xs font-semibold tracking-widest text-accent">ACTIVE ETF · PERIOD</p>
        <h2 className="mt-2 text-2xl font-semibold text-text-primary">區間持股觀察</h2>
        <p className="mt-2 text-sm leading-relaxed text-text-muted">
          觀察一段期間內，各基金對個股的持股增減與每日變化。
        </p>
      </header>
      <section className="ui-panel space-y-4 p-4 sm:p-5" aria-label="區間篩選">
        <form
          className="grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap"
          onSubmit={(event) => {
            event.preventDefault();
            if (!invalidRange) update({ start: draftStart, end: draftEnd });
          }}
        >
          <label className="min-w-0 sm:flex-none">
            <span className="mb-1.5 block text-xs text-text-muted">開始日期</span>
            <input
              type="date"
              className="ui-input"
              value={draftStart}
              onChange={(event) => setDraftStart(event.target.value)}
              required
            />
          </label>
          <label className="min-w-0 sm:flex-none">
            <span className="mb-1.5 block text-xs text-text-muted">結束日期</span>
            <input
              type="date"
              className="ui-input"
              value={draftEnd}
              onChange={(event) => setDraftEnd(event.target.value)}
              required
            />
          </label>
          <button type="submit" className="ui-button-primary col-span-2" disabled={invalidRange}>
            套用區間
          </button>
          <div className="col-span-2 flex flex-wrap gap-2">
            {[7, 30].map((days) => (
              <button
                key={days}
                type="button"
                className="ui-button-secondary"
                disabled={!data?.available_dates[0]}
                onClick={() => {
                  const latest = data!.available_dates[0]!;
                  update({ start: shiftDate(latest, 1 - days), end: latest });
                }}
              >
                {days === 7 ? "近一週" : "近一個月"}
              </button>
            ))}
          </div>
        </form>
        {invalidRange && draftStart && draftEnd && (
          <p role="alert" className="text-sm text-negative">
            開始日期不可晚於結束日期，區間最多 366 天。
          </p>
        )}
        <label className="block sm:max-w-xs">
          <span className="mb-1.5 block text-xs text-text-muted">搜尋個股</span>
          <input
            type="search"
            className="ui-input"
            placeholder="輸入股號或名稱"
            value={search}
            onChange={(event) => update({ q: event.target.value || null, stock: null }, true)}
          />
        </label>
      </section>
      {query.isPending ? (
        <p role="status" className="py-8 text-center text-text-muted">
          正在讀取區間持股資料…
        </p>
      ) : query.isError ? (
        <WorkspaceEmptyState
          eyebrow="Period"
          title="區間持股暫時無法載入"
          description="請確認日期範圍，或稍後重試；尚未累積快照時也無法顯示區間資料。"
          actions={
            <button type="button" className="ui-button-secondary" onClick={() => void query.refetch()}>
              重新讀取
            </button>
          }
        />
      ) : (
        data && (
          <>
            <section className="space-y-3" aria-label="區間資料涵蓋">
              <p className="text-sm font-medium">
                {data.start_date} → {data.end_date} · {data.observed_dates.length} 個資料日 · {stocks.length} 檔個股
              </p>
              <p className="text-xs leading-relaxed text-text-muted">
                每檔基金以區間內首末快照比較；第一份快照為基準，不計入增減天數。各基金比較日期可能不同，股數不跨基金加總。
              </p>
              <details className="rounded-xl border border-border p-3 text-xs text-text-muted">
                <summary className="cursor-pointer font-medium">
                  基金資料涵蓋：{data.funds.filter((fund) => fund.snapshot_count >= 2).length} / {data.funds.length}{" "}
                  檔可比較
                </summary>
                <p className="mt-3">缺口依其他基金有快照的資料日判定；未涵蓋所有市場休市日或全體來源中斷的情況。</p>
                <ul className="mt-3 space-y-2">
                  {data.funds.map((fund) => (
                    <li key={fund.fund_code}>
                      <span className="font-mono">{fund.fund_code}</span> {fund.fund_name} ·{" "}
                      {fund.snapshot_count === 0
                        ? "區間內無快照"
                        : `${fund.first_date} → ${fund.last_date} · ${fund.snapshot_count} 份快照`}
                      {fund.snapshot_count === 1 && " · 僅一份，無法比較"}
                      {fund.missing_dates.length > 0 && (
                        <span className="text-signal">
                          {" "}
                          · 缺 {fund.missing_dates.length} 個資料日（{fund.missing_dates.join("、")}）
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            </section>
            {data.observed_dates.length === 0 ? (
              <WorkspaceEmptyState
                eyebrow="Period"
                title="這段期間尚無持股快照"
                description="請選擇其他日期範圍；沒有快照不代表基金沒有操作。"
              />
            ) : stocks.length === 0 ? (
              <WorkspaceEmptyState
                eyebrow="Period"
                title="找不到符合篩選條件的個股"
                description="請調整股號、名稱或日期範圍。"
              />
            ) : (
              <section className="space-y-3" aria-label="區間個股彙整">
                <h3 className="text-sm font-semibold">個股持股動向</h3>
                <div className="overflow-hidden rounded-xl border border-border">
                  {stocks.slice(0, visibleCount).map((stock) => {
                    const increased = stock.funds.filter((fund) => (fund.net_share_delta ?? 0) > 0).length;
                    const decreased = stock.funds.filter((fund) => (fund.net_share_delta ?? 0) < 0).length;
                    const unchanged = stock.funds.filter((fund) => fund.net_share_delta === 0).length;
                    const unknown = stock.funds.filter((fund) => fund.net_share_delta === null).length;
                    return (
                      <button
                        key={stock.symbol}
                        type="button"
                        className="ui-data-row flex w-full flex-wrap items-center justify-between gap-3 text-left hover:bg-card-hover"
                        aria-label={`查看 ${stock.symbol} ${stock.name} 區間明細`}
                        aria-expanded={selected === stock.symbol}
                        onClick={() =>
                          update({
                            stock: selected === stock.symbol ? null : stock.symbol,
                            start: data.start_date,
                            end: data.end_date,
                          })
                        }
                      >
                        <span>
                          <span className="font-mono font-semibold">{stock.symbol}</span>{" "}
                          <span className="text-sm text-text-muted">{stock.name}</span>
                          <span className="mt-1 block text-xs text-text-faint">
                            {stock.funds.length} 檔基金曾持有 · 依基金數排序
                          </span>
                        </span>
                        <span className="flex flex-wrap gap-3 text-xs">
                          <span className="text-positive">淨增加 {increased}</span>
                          <span className="text-negative">淨減少 {decreased}</span>
                          <span>持平 {unchanged}</span>
                          {unknown > 0 && <span>無法比較 {unknown}</span>}
                        </span>
                      </button>
                    );
                  })}
                </div>
                {stocks.length > visibleCount && (
                  <button
                    type="button"
                    className="ui-button-secondary"
                    onClick={() => setVisibleCount((count) => count + 50)}
                  >
                    顯示更多個股
                  </button>
                )}
              </section>
            )}
            {selectedStock && (
              <section
                className="ui-panel min-w-0 space-y-4 p-4 sm:p-5"
                aria-label={`${selectedStock.symbol} 區間明細`}
                ref={detailSection}
                tabIndex={-1}
              >
                <div className="flex items-center justify-between gap-3">
                  <h3 className="font-semibold">
                    {selectedStock.symbol} {selectedStock.name}
                  </h3>
                  <button type="button" className="ui-button-secondary" onClick={() => update({ stock: null })}>
                    收合明細
                  </button>
                </div>
                <div className="grid gap-3">
                  {selectedStock.funds.map((fund) => (
                    <article className="rounded-xl border border-border p-3" key={fund.fund_code}>
                      <h4 className="text-sm font-medium">
                        {fund.fund_code} {fund.fund_name}
                      </h4>
                      <p className="mt-1 text-xs text-text-muted">
                        實際比較 {fund.first_date} → {fund.last_date}
                      </p>
                      {data.funds.find((item) => item.fund_code === fund.fund_code)?.missing_dates.length ? (
                        <p className="mt-2 text-xs text-signal">
                          此基金有資料日缺口；增減天數僅計有快照的比較日，無法還原缺口內的每日操作。
                        </p>
                      ) : null}
                      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                        <div>
                          <dt className="text-xs text-text-muted">期初 → 期末股數</dt>
                          <dd className="mt-1 tabular-nums">
                            {number(fund.first_shares)} → {number(fund.last_shares)}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-xs text-text-muted">股數淨增減</dt>
                          <dd className="mt-1 tabular-nums">{number(fund.net_share_delta, true)}</dd>
                        </div>
                        <div>
                          <dt className="text-xs text-text-muted">期初 → 期末權重</dt>
                          <dd className="mt-1 tabular-nums">
                            {number(fund.first_weight_pct)}% → {number(fund.last_weight_pct)}%
                          </dd>
                        </div>
                        <div>
                          <dt className="text-xs text-text-muted">權重差</dt>
                          <dd className="mt-1 tabular-nums">
                            {number(fund.weight_delta_pct_points, true)}
                            {fund.weight_delta_pct_points !== null && " pp"}
                          </dd>
                        </div>
                      </dl>
                      <p className="mt-3 text-xs text-text-muted">
                        增加 {fund.increase_days} 天 · 減少 {fund.decrease_days} 天 · 其中新增持股 {fund.added_days}{" "}
                        天、退出持股 {fund.removed_days} 天
                      </p>
                      {fund.scale_change_days > 0 && (
                        <p className="mt-2 text-xs text-signal">{fund.scale_change_days} 天可能受基金規模變動影響</p>
                      )}
                    </article>
                  ))}
                </div>
                <h4 className="text-sm font-semibold">每日持股明細</h4>
                {detail.isPending ? (
                  <p role="status">正在讀取每日明細…</p>
                ) : detail.isError ? (
                  <div role="alert">
                    每日明細載入失敗。
                    <button className="ui-button-secondary" onClick={() => void detail.refetch()}>
                      重試明細
                    </button>
                  </div>
                ) : (
                  detail.data && <RangeTimeline data={detail.data} onDaily={openDaily} />
                )}
              </section>
            )}
          </>
        )
      )}
      <p className="border-t border-border pt-4 text-xs leading-relaxed text-text-faint">
        來源為 MoneyDJ
        公開持股快照。股數變化可能受基金申贖影響，權重也受價格變動影響；這裡呈現持股變化，不代表實際成交買賣超。
      </p>
    </div>
  );
}

function RangeTimeline({ data, onDaily }: { data: ActiveEtfRangeResponse; onDaily: (date: string) => void }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full whitespace-nowrap text-left text-xs">
        <thead className="bg-surface-raised text-text-muted">
          <tr>
            {["資料日／前期", "基金", "股數", "權重", "股數增減", "觀察", "來源／明細"].map((label) => (
              <th className="p-3" key={label}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.timeline.map((point) => (
            <tr key={`${point.fund_code}-${point.data_date}`} className="border-t border-border">
              <td className="p-3 font-mono">
                {point.data_date}
                <span className="mt-1 block text-text-faint">{point.previous_date ?? "區間首份基準"}</span>
              </td>
              <td className="p-3 font-mono">{point.fund_code}</td>
              <td className="p-3 tabular-nums">{number(point.shares)}</td>
              <td className="p-3 tabular-nums">{number(point.weight_pct)}%</td>
              <td className="p-3 tabular-nums">
                {point.share_delta === null ? "基準" : number(point.share_delta, true)}
              </td>
              <td className="p-3">
                {point.action ? ACTIVE_ETF_ACTION_LABEL[point.action] : point.previous_date ? "股數未變" : "基準"}
                {point.likely_fund_scale_change && <span className="block text-signal">可能受基金規模影響</span>}
              </td>
              <td className="p-3">
                <a className="text-accent underline" href={point.source_url} target="_blank" rel="noreferrer">
                  MoneyDJ
                </a>
                <span className="mt-1 block text-text-faint">擷取 {point.fetched_at}</span>
                <button
                  type="button"
                  className="mt-2 text-accent underline"
                  aria-label={`查看 ${point.data_date} ${point.fund_code} 單日觀察`}
                  onClick={() => onDaily(point.data_date)}
                >
                  查看單日
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
