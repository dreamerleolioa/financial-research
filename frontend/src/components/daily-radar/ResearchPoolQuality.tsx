import { useState } from "react";
import type { DailyRadarResearchComparison } from "../../lib/dailyRadarTypes";

const groups = [["selected", "全部入池"], ["top_3", "每日前 3 檔"], ["top_5", "每日前 5 檔"],
  ["comparable_shadow", "可比較未入選"]] as const;
const percent = (value: number | null, rate = false) => value === null ? "資料不足或尚待累積"
  : `${(value * (rate ? 100 : 1)).toFixed(1)}%`;
const reasons: Record<string, string> = {
  missing_entry_open: "缺少股票次日開盤價", missing_benchmark_entry_open: "缺少大盤次日開盤價",
  price_basis_mismatch: "價格口徑不一致", research_basis_mismatch: "保存結果口徑不一致",
  price_provenance_missing: "價格來源不明", corporate_action_provenance_missing: "除權息資料待確認",
  stock_split_in_window: "期間包含拆併股，暫不比較", conflicting_price_records: "價格紀錄衝突",
  invalid_research_ohlc: "價格資料無效", missing_future_price: "缺少後續股票行情",
  missing_benchmark: "缺少大盤交易日資料", window_not_mature: "尚未滿期",
  missing_outcome: "尚未保存驗證結果", missing_return_metric: "報酬資料不足",
  missing_risk_metric: "跌幅資料不足", signal_date_missing: "訊號日期不明", future_signal_date: "訊號日期尚未到達",
};
const confidenceLabels = {
  insufficient_blocks: "樣本不足", incomplete_coverage: "比較資料不足",
  calendar_missing: "交易日資料不足", sparse_comparable_dates: "可配對日期不足",
  estimated: "已取得估計區間",
  strategy_unknown: "策略版本不完整",
};

export function ResearchPoolQuality({ comparison, windowDays }: {
  comparison: DailyRadarResearchComparison; windowDays: number;
}) {
  const [cost, setCost] = useState("0");
  const report = comparison.cost_scenarios[cost];
  if (!report) return <p>此成本情境尚無已保存研究資料。</p>;
  const ci = report.confidence;
  return (
    <section data-testid="research-pool-quality" className="rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel md:p-5">
      <h3 className="font-semibold text-text-primary">候選池選股與排序品質 · {windowDays} 日 · 次日開盤</h3>
      <p className="mt-2 text-xs leading-relaxed text-text-muted">
        從訊號後下一個交易日開盤，計算到第 {windowDays} 個交易日收盤；股票與大盤使用相同期間。
        價格報酬不含股息，並非實際成交績效。股票報酬扣除下方假設成本，大盤維持未扣成本的價格報酬。
      </p>
      <label className="mt-3 block text-xs font-medium text-text-muted">
        假設總成本
        <select value={cost} onChange={(e) => setCost(e.target.value)}
          className="ml-3 min-h-10 rounded-[8px] border border-border bg-surface px-3 text-sm text-text-primary">
          {["0", "0.5", "1"].map((value) => <option key={value} value={value}>{value}%</option>)}
        </select>
      </label>
      <p className="mt-1 text-xs text-text-muted">每筆價格報酬扣除 {cost} 個百分點，用於成本敏感度比較，並非你的實際費率。</p>
      {!comparison.last_evaluated_date && <p className="mt-3 text-sm text-text-secondary">
        尚未保存次日開盤驗證結果。需要驗證排程完成，或等待窗口滿期後才有數字。
      </p>}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[1100px] text-sm">
          <thead><tr className="border-b border-border-subtle text-xs text-text-muted">
            {["樣本組別", "有效／總數", "訊號日／股票數", "價格報酬中位數", "超額報酬中位數",
              "超越大盤比例", "最差 10% 平均報酬", "期間最深跌幅", "跌幅中位數", "未滿期／缺漏／跳過"].map((label) =>
              <th key={label} scope="col" className="px-2 py-3 text-left font-medium">{label}</th>)}
          </tr></thead>
          <tbody>{groups.map(([key, label]) => {
            const stats = report[key];
            return <tr key={key} className="border-b border-border-subtle last:border-0">
              <th scope="row" className="px-2 py-3 text-left font-medium text-text-secondary">{label}</th>
              <td className="px-2 py-3 tabular-nums">{stats.evaluated_count}／{stats.sample_count}</td>
              <td className="px-2 py-3 tabular-nums">{stats.signal_date_count}／{stats.distinct_symbol_count}</td>
              <td className="px-2 py-3 tabular-nums">{percent(stats.median_return_pct)}</td>
              <td className="px-2 py-3 tabular-nums">{percent(stats.median_excess_return_pct)}</td>
              <td className="px-2 py-3 tabular-nums">{percent(stats.positive_excess_rate, true)}</td>
              <td className="px-2 py-3 tabular-nums">{stats.evaluated_count < 10 && stats.coverage_complete
                ? "需至少 10 筆樣本" : percent(stats.worst_decile_mean_return_pct)}</td>
              <td className="px-2 py-3 tabular-nums">{percent(stats.worst_adverse_excursion_pct)}</td>
              <td className="px-2 py-3 tabular-nums">{percent(stats.median_adverse_excursion_pct)}</td>
              <td className="px-2 py-3 tabular-nums">{stats.immature_count}／{stats.missing_outcome_count + stats.missing_metric_count}／{stats.skipped_count}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-text-muted md:hidden">表格可左右滑動，查看完整指標。</p>
      <p className="mt-3 text-xs leading-relaxed text-text-muted">
        跌幅相對次日開盤價計算，涵蓋期間盤中低點，未扣成本；最差一成平均採向上取整，至少需 10 筆有效樣本。
        每日重複訊號是觀察樣本，不能當成獨立交易。表格各組日期與股票組成可能不同。
      </p>
      <p className="mt-2 text-xs text-text-muted">已觀察可比較樣本中，超越基準機會的入池占比：
        {percent(report.observed_positive_capture_share, true)}。此占比涵蓋已觀察樣本，非全市場召回率。</p>
      <div data-testid="research-confidence" className="mt-4 border-t border-border-subtle pt-3 text-xs leading-relaxed text-text-muted">
        <p className="font-medium text-text-secondary">入池與未入選比較的可信度：{confidenceLabels[ci.status]}</p>
        <p>僅比較同日兩組皆有資料的每日平均超額報酬。
          {ci.status === "incomplete_coverage" || ci.status === "strategy_unknown" ? "配對日期與完整區塊尚無法確認。" : `${ci.paired_date_count} 個配對日期，${ci.effective_block_count === null ? "完整日期區塊無法判定" : `${ci.effective_block_count} 個完整日期區塊`}。`}
          每區塊 {ci.block_trading_days} 個交易日，至少需 {ci.minimum_blocks} 個區塊。</p>
        {ci.status === "estimated" && <p>95% 估計區間：{percent(ci.lower_pct)} ～ {percent(ci.upper_pct)}；
          入池減未入選的每日平均差：{percent(ci.mean_difference_pct)}。區間跨越 0 時，尚無明確優勢證據。</p>}
        <p>估計採日期區塊抽樣，保留重疊窗口的相依性；固定總成本在兩組差值中抵銷。估計區間不保證未來表現。</p>
      </div>
      <details className="mt-3 text-xs text-text-muted">
        <summary className="cursor-pointer">缺資料原因與結果版本</summary>
        {groups.map(([key, label]) => <p key={key} className="mt-2">{label}：
          {Object.entries(report[key].missing_reasons).map(([reason, count]) => `${reasons[reason] ?? "其他資料待確認"} ${count} 筆`).join("；") || "沒有缺資料紀錄"}</p>)}
        <p className="mt-2">已保存驗證日期：{comparison.last_evaluated_date ?? "尚未保存"}；版本：{comparison.validation_version}。</p>
        <p>基準：{report.selected.benchmark_symbols.join("、") || "待確認"}；資格不符樣本不混入對照。</p>
        <p>交易日資料涵蓋至：{comparison.calendar_through_date ?? "交易日證據不足，缺結果無法判定是否滿期"}。</p>
      </details>
    </section>
  );
}
