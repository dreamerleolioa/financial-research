import type { DailyRadarPoolComparison, DailyRadarPoolQualityStats } from "../../lib/dailyRadarTypes";

const labels = [
  ["selected", "全部入池"], ["top_3", "每日前 3 檔"], ["top_5", "每日前 5 檔"],
  ["comparable_shadow", "可比較未入選"],
] as const;
function metric(stats: DailyRadarPoolQualityStats, value: number | null, rate = false) {
  return value === null ? (stats.coverage_complete ? "尚待累積" : "資料不足")
    : `${(value * (rate ? 100 : 1)).toFixed(1)}%`;
}

export function PoolQuality({ comparison, windowDays }: { comparison: DailyRadarPoolComparison; windowDays: number }) {
  return (
    <section className="rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel md:p-5">
      <h3 className="font-semibold text-text-primary">候選池選股與排序品質 · {windowDays} 日</h3>
      <p className="mt-1 text-xs leading-relaxed text-text-muted">
        比較同策略已觀察的入池與可比較未入選樣本，資格不符樣本不混入對照。
        每日重複訊號列入統計，非獨立交易樣本；不同組別的日期與股票組成可能不同。
        相對基準報酬以訊號日價格計算，未扣交易成本。
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[660px] text-sm">
          <thead><tr className="border-b border-border-subtle text-xs text-text-muted">
            {["樣本組別", "有效／總數", "訊號日／股票數", "超越基準比例", "超額報酬中位數", "未滿期／缺漏／跳過"].map((label) =>
              <th key={label} scope="col" className="px-2 py-3 text-left font-medium">{label}</th>)}
          </tr></thead>
          <tbody>{labels.map(([key, label]) => {
            const stats = comparison[key];
            return <tr key={key} className="border-b border-border-subtle last:border-0">
              <th scope="row" className="px-2 py-3 text-left font-medium text-text-secondary">{label}</th>
              <td className="px-2 py-3 tabular-nums">{stats.evaluated_count}／{stats.sample_count}</td>
              <td className="px-2 py-3 tabular-nums">{stats.signal_date_count}／{stats.distinct_symbol_count}</td>
              <td className="px-2 py-3 tabular-nums">{metric(stats, stats.positive_excess_rate, true)}</td>
              <td className="px-2 py-3 tabular-nums">{metric(stats, stats.median_excess_return_pct)}</td>
              <td className="px-2 py-3 tabular-nums">{stats.immature_count}／{stats.missing_outcome_count + stats.missing_metric_count}／{stats.skipped_count}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-text-muted">
        已觀察可比較樣本中，超越基準機會的入池占比：
        <span data-testid="pool-capture-share" className="font-semibold text-text-secondary">
          {comparison.observed_positive_capture_share === null ? "資料不足或尚待累積" : `${(comparison.observed_positive_capture_share * 100).toFixed(1)}%`}
        </span>。此占比僅涵蓋已觀察樣本，非全市場召回率。
      </p>
      <p className="mt-2 text-xs text-text-muted">
        入池基準：{comparison.selected.benchmark_symbols.join("、") || "待確認"}；
        未入選基準：{comparison.comparable_shadow.benchmark_symbols.join("、") || "待確認"}。
        資料缺漏、跳過或基準不一致時，不發布完整比較比例。
      </p>
    </section>
  );
}
