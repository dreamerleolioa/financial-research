import { useState } from "react";
import type { DailyRadarObservationStats, DailyRadarValidationResponse } from "../../lib/dailyRadarTypes";
import { PoolQuality } from "./PoolQuality";

const panel = "rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel md:p-5";
const percent = (value: number | null, scale = 1) => (value === null ? "—" : `${(value * scale).toFixed(1)}%`);
const rate = (stats: DailyRadarObservationStats, value: number | null) =>
  value === null ? (stats.coverage_complete ? "尚待累積" : "資料不足") : percent(value, 100);
const mean = (value: number | null, suffix: string) => (value === null ? "—" : `${value.toFixed(1)}${suffix}`);

const reasonLabels: Record<string, string> = {
  candidate_history_gap_or_invalid_ohlc: "股價歷史缺漏或價格無效",
  reference_date_unknown_or_stale: "參考價格日期不明或落後",
  reference_missing_or_invalid: "缺少有效支撐／壓力參考",
  origin_unknown: "首次觀察紀錄不足",
  origin_date_invalid: "首次觀察日期無效",
  window_not_mature: "未取得完整觀察窗口",
  insufficient_forward_trading_days: "未取得完整後續交易日",
  stale_candidate_price: "訊號價格日期落後",
  missing_candidate_price: "缺少訊號價格",
};

function Metric({ label, value, helper, testId }: { label: string; value: string; helper: string; testId?: string }) {
  return (
    <div className="min-w-0 border-t border-border-subtle px-4 py-4 md:px-5 lg:border-l lg:border-t-0 lg:first:border-l-0">
      <p className="text-xs font-medium text-text-faint">{label}</p>
      <p data-testid={testId} className="mt-2 text-xl font-semibold tabular-nums text-text-primary">
        {value}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-text-muted">{helper}</p>
    </div>
  );
}

function Choice({ active, children, onClick }: { active: boolean; children: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`min-h-10 rounded-[8px] px-3 text-sm font-medium transition-colors duration-150 ${
        active ? "bg-accent text-accent-contrast" : "text-text-muted hover:bg-card-hover hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}

export function ValidationResults({
  data,
  loading,
  error,
  onRetry,
}: {
  data: DailyRadarValidationResponse | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const [windowDays, setWindowDays] = useState(5);
  const [priority, setPriority] = useState<3 | 5>(3);
  const [cohortId, setCohortId] = useState<string | null>(null);
  const cohort =
    data?.cohorts.find((c) => c.id === (cohortId ?? data.default_cohort_id)) ??
    data?.cohorts.find((c) => c.id === data.default_cohort_id) ??
    data?.cohorts[0];
  const groups = cohort?.windows[String(windowDays)];
  const focused = groups?.[`top_${priority}`];
  const poolComparison = cohort?.pool_comparison?.[String(windowDays)];

  return (
    <div className="space-y-5">
      {Boolean(error) && (
        <div role="alert" className={`${panel} space-y-2`}>
          <h2 className="font-semibold text-text-primary">驗證結果暫時無法載入</h2>
          <p className="text-sm text-text-muted">讀取失敗，請稍後重試。</p>
          {data && <p className="text-sm text-text-muted">更新失敗，以下保留上次成功讀取的驗證結果。</p>}
          <button type="button" onClick={onRetry} className="ui-button-secondary">
            重新讀取驗證結果
          </button>
        </div>
      )}
      {loading && !data && (
        <section className={panel} role="status">
          <p className="font-semibold text-text-primary">正在讀取驗證結果…</p>
          <p className="mt-2 text-sm text-text-muted">讀取已保存的統計，完成後會顯示樣本期間與資料完整度。</p>
        </section>
      )}
      {data && (
        <>
          <section className={panel}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-text-primary">突破前觀察驗證</h2>
                <p className="mt-1 text-sm leading-relaxed text-text-muted">
                  檢查雷達是否提早發現趨勢，以及每日優先關注的標的是否提供更多有效機會。
                </p>
              </div>
              <span className="ui-badge">5／10／20 交易日</span>
            </div>
            <dl className="mt-4 grid gap-3 text-xs text-text-muted sm:grid-cols-2">
              <div>
                <dt>樣本範圍（最近 {data.lookback_days} 日）</dt>
                <dd className="mt-1 tabular-nums text-text-secondary">
                  {data.sample_start_date} ～ {data.sample_end_date}
                </dd>
              </div>
              <div>
                <dt>最後已保存驗證日期</dt>
                <dd className="mt-1 tabular-nums text-text-secondary">
                  {data.last_evaluated_date ?? "尚未保存驗證結果"}
                </dd>
              </div>
              <div>
                <dt>統計截止日</dt>
                <dd className="mt-1 tabular-nums text-text-secondary">{data.as_of_date}</dd>
              </div>
              <div>
                <dt>交易日資料涵蓋至</dt>
                <dd className="mt-1 tabular-nums text-text-secondary">
                  {data.calendar_through_date ?? "交易日資料不足，無法判定未計算樣本是否滿期"}
                </dd>
              </div>
            </dl>
            {cohort && (
              <div className="mt-4 border-t border-border-subtle pt-4">
                <label htmlFor="validation-strategy" className="text-xs font-medium text-text-muted">
                  策略批次（不同版本分開統計）
                </label>
                <select
                  id="validation-strategy"
                  value={cohort.id}
                  onChange={(e) => setCohortId(e.target.value)}
                  className="mt-2 block min-h-10 w-full rounded-[8px] border border-border bg-surface px-3 text-sm text-text-primary"
                >
                  {data.cohorts.map((c, index) => (
                    <option key={c.id} value={c.id}>
                      {c.id === data.default_cohort_id ? "最新策略" : `歷史策略 ${index + 1}`} ·{" "}
                      {c.signal_start_date.slice(5)} ～ {c.signal_end_date.slice(5)}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs tabular-nums text-text-muted">
                  入選期間：{cohort.signal_start_date} ～ {cohort.signal_end_date}；完整版本見下方說明。
                </p>
              </div>
            )}
          </section>
          {!cohort && (
            <section className={panel}>
              <h3 className="font-semibold text-text-primary">目前沒有可統計的觀察批次</h3>
              <p className="mt-2 text-sm text-text-muted">
                樣本期間內尚無公開入選紀錄。每日雷達與驗證持續累積後，結果會出現在這裡。
              </p>
            </section>
          )}
          {focused && groups && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <div aria-label="觀察期間" className="flex rounded-[10px] border border-border bg-surface-raised p-1">
                  {[5, 10, 20].map((day) => (
                    <Choice
                      key={day}
                      active={windowDays === day}
                      onClick={() => setWindowDays(day)}
                    >{`${day} 日`}</Choice>
                  ))}
                </div>
                <div
                  aria-label="優先候選範圍"
                  className="flex rounded-[10px] border border-border bg-surface-raised p-1"
                >
                  {([3, 5] as const).map((n) => (
                    <Choice key={n} active={priority === n} onClick={() => setPriority(n)}>{`每日前 ${n} 檔`}</Choice>
                  ))}
                </div>
              </div>
              {poolComparison && <PoolQuality comparison={poolComparison} windowDays={windowDays} />}
              {!focused.evaluated_observation_count && (
                <section className={panel}>
                  <h3 className="font-semibold text-text-primary">尚未累積可評估結果</h3>
                  <p className="mt-2 text-sm text-text-muted">
                    目前沒有可評估的首次觀察樣本。等待期間與缺資料原因列在下方，這不代表策略失敗。
                  </p>
                </section>
              )}
              <section className="overflow-hidden rounded-[14px] border border-border bg-surface-raised shadow-panel">
                <div className="border-b border-border-subtle px-4 py-3 md:px-5">
                  <h3 className="text-sm font-semibold text-text-primary">
                    每日前 {priority} 檔 · {windowDays} 日觀察
                  </h3>
                  <p className="mt-1 text-xs text-text-muted">
                    {focused.evaluated_observation_count} 筆有效首次觀察，來自 {focused.evaluated_signal_date_count}{" "}
                    個訊號日、{focused.evaluated_distinct_symbol_count} 檔股票。
                  </p>
                </div>
                <div className="grid grid-cols-2 [&>*:nth-child(-n+2)]:border-t-0 lg:grid-cols-4">
                  <Metric
                    label="突破確認率"
                    value={rate(focused, focused.confirmation_rate)}
                    helper="連續兩個交易日收盤高於首次壓力價"
                    testId="confirmation-rate"
                  />
                  <Metric
                    label="突破前失效率"
                    value={rate(focused, focused.invalidation_rate)}
                    helper="突破確認前，收盤跌破首次支撐價"
                  />
                  <Metric
                    label="平均提前天數"
                    value={mean(focused.mean_lead_trading_days, " 天")}
                    helper={`${focused.coverage_complete ? "" : "部分樣本 · "}僅計已確認突破，按交易日計算`}
                  />
                  <Metric
                    label="平均等待最大不利波動"
                    value={percent(focused.mean_waiting_max_adverse_excursion_pct)}
                    helper={`${focused.coverage_complete ? "" : "部分樣本 · "}以首次訊號收盤至等待結束期間低點計算`}
                  />
                </div>
              </section>
              <section className={panel}>
                <h3 className="font-semibold text-text-primary">優先候選與其餘候選比較</h3>
                <p className="mt-1 text-xs leading-relaxed text-text-muted">
                  每日完整名單先排名，再比對驗證結果；缺資料的高排名標的不會被其他股票補位。
                </p>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[540px] text-sm">
                    <caption className="sr-only">
                      {windowDays} 交易日的每日前 {priority} 檔、其餘候選與全部候選統計
                    </caption>
                    <thead>
                      <tr className="border-b border-border">
                        <th scope="col" className="pb-3 pr-4 text-left font-medium text-text-muted">
                          指標
                        </th>
                        {[`每日前 ${priority} 檔`, "其餘候選", "全部候選"].map((label) => (
                          <th scope="col" key={label} className="px-3 pb-3 text-right font-medium text-text-primary">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        {
                          label: "有效首次觀察",
                          value: (s: DailyRadarObservationStats) => String(s.evaluated_observation_count),
                        },
                        {
                          label: "有效訊號日",
                          value: (s: DailyRadarObservationStats) => String(s.evaluated_signal_date_count),
                        },
                        { label: "突破確認率", value: (s: DailyRadarObservationStats) => rate(s, s.confirmation_rate) },
                        {
                          label: "突破前失效率",
                          value: (s: DailyRadarObservationStats) => rate(s, s.invalidation_rate),
                        },
                        {
                          label: "平均提前天數",
                          value: (s: DailyRadarObservationStats) => mean(s.mean_lead_trading_days, " 天"),
                        },
                        {
                          label: "平均等待最大不利波動",
                          value: (s: DailyRadarObservationStats) => percent(s.mean_waiting_max_adverse_excursion_pct),
                        },
                        {
                          label: "資料完整度",
                          value: (s: DailyRadarObservationStats) => (s.coverage_complete ? "完整" : "資料不足"),
                        },
                      ].map((row) => (
                        <tr key={row.label} className="border-b border-border-subtle last:border-b-0">
                          <th scope="row" className="py-3 pr-4 text-left font-normal text-text-muted">
                            {row.label}
                          </th>
                          {[focused, groups[`remaining_after_${priority}`], groups.all_selected].map((s, i) => (
                            <td key={i} className="px-3 py-3 text-right tabular-nums text-text-primary">
                              {row.value(s)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-xs text-text-faint sm:hidden">左右滑動表格，可查看其餘與全部候選。</p>
                <p className="mt-3 text-xs leading-relaxed text-text-faint">
                  平均值僅反映已取得的有效樣本；資料不足時不顯示整體比率，也不把尚未突破當成失敗。
                </p>
              </section>
              <section className={panel}>
                <h3 className="font-semibold text-text-primary">每日前 {priority} 檔的樣本完整度</h3>
                <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-4 text-xs sm:grid-cols-3">
                  {[
                    ["資料尚未涵蓋完整觀察期", focused.immature_observation_count],
                    ["尚未計算突破診斷", focused.missing_diagnostic_count],
                    ["缺少已保存驗證結果", focused.missing_outcome_count],
                    ["重複觀察（不重算）", focused.excluded_repeat_count],
                    [
                      "驗證資料不足或已跳過",
                      focused.skipped_validation_count + (focused.status_counts.insufficient_data ?? 0),
                    ],
                    ["滿期仍未突破", focused.status_counts.unconfirmed ?? 0],
                    ["首次入選時已突破", focused.status_counts.already_broken_out ?? 0],
                    ["首次入選時已跌破支撐", focused.status_counts.already_invalidated ?? 0],
                  ].map(([label, count]) => (
                    <div key={label}>
                      <dt className="leading-relaxed text-text-muted">{label}</dt>
                      <dd className="mt-1 text-base font-semibold tabular-nums text-text-primary">{count}</dd>
                    </div>
                  ))}
                </dl>
                {!focused.ranking_pool_complete && (
                  <p className="mt-4 text-sm text-text-muted">當日完整排名名單不足，因此暫不顯示比率。</p>
                )}
                {Object.keys(focused.missing_reasons).length > 0 && (
                  <details className="mt-4 border-t border-border-subtle pt-3">
                    <summary className="min-h-10 cursor-pointer text-sm text-text-secondary">查看資料不足原因</summary>
                    <ul className="space-y-2 text-xs text-text-muted">
                      {Object.entries(focused.missing_reasons).map(([reason, count]) => (
                        <li key={reason}>
                          {reasonLabels[reason] ?? `其他資料問題（${reason}）`}：{count} 筆
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </section>
              <details className={panel}>
                <summary className="min-h-10 cursor-pointer text-sm font-medium text-text-secondary">
                  如何解讀這些結果
                </summary>
                <ul className="mt-2 space-y-2 text-xs leading-relaxed text-text-muted">
                  <li>同一股票、同一策略版本，只計可取得公開歷史中的首次入選；之後重複出現不增加有效樣本。</li>
                  <li>
                    固定使用首次入選的支撐與壓力價。連續兩日收盤高於壓力才確認突破；確認前收盤跌破支撐，才算突破前失效。
                  </li>
                  <li>
                    平均提前天數僅計已突破樣本；平均等待最大不利波動包含有效樣本等待至突破、失效或觀察期結束的低點。
                  </li>
                  <li>這些統計衡量觀察機會與等待風險；舊驗證若缺少突破診斷，會顯示尚未計算。</li>
                </ul>
                {cohort && (
                  <dl className="mt-4 grid gap-2 border-t border-border-subtle pt-3 text-xs sm:grid-cols-2">
                    {Object.entries(cohort.strategy).map(([key, value]) => (
                      <div key={key}>
                        <dt className="text-text-faint">
                          {(
                            {
                              scoring_version: "評分版本",
                              rule_version: "規則版本",
                              config_version: "設定版本",
                              selection_version: "選股版本",
                            } as Record<string, string>
                          )[key] ?? key}
                        </dt>
                        <dd className="mt-1 break-all text-text-muted">{value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </details>
            </>
          )}
        </>
      )}
    </div>
  );
}
