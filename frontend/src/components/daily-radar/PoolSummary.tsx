import type { DailyRadarPoolSummary } from "../../lib/dailyRadarTypes";

const stateLabels: Record<string, string> = {
  selected: "已入池", data_pending: "資料待確認", eligibility_excluded: "資格排除",
  signal_filtered: "訊號未達標", limit_deferred: "名額限制", processing_error: "處理失敗",
};
const reasonLabels: Record<string, string> = {
  data_gap: "必要資料缺漏", stale_core_data: "核心資料過期", low_liquidity: "流動性不足",
  min_price: "價格未達門檻", overextended: "短線過熱", weak_structure: "結構偏弱",
  margin_crowding: "融資擁擠", unsupported_daily_radar_symbol: "標的不適用",
};

export function PoolSummary({ summary }: { summary: DailyRadarPoolSummary }) {
  return (
    <section className="rounded-[14px] border border-border bg-surface-raised p-4 shadow-panel md:p-5">
      <h2 className="text-base font-semibold text-text-primary">候選池篩選摘要</h2>
      <p className="mt-1 text-xs leading-relaxed text-text-muted">
        本批次掃描 {summary.input_record_count} 筆資料列；統計範圍為已取得資料的掃描標的，非全市場涵蓋率。數量涵蓋完整批次，不受下方分類或顯示筆數影響。
      </p>
      {summary.discovery_summary ? (
        <p className="mt-2 text-xs leading-relaxed text-text-secondary">
          市場價量探索（{summary.discovery_summary.run_date}）：掃描 {summary.discovery_summary.scanned_symbol_count} 檔，
          符合資料與流動性門檻 {summary.discovery_summary.eligible_symbol_count} 檔，
          保留探索候選 {summary.discovery_summary.discovered_symbol_count} 檔。探索後仍需取得還原行情並通過評分。
        </p>
      ) : <p className="mt-2 text-xs text-text-muted">此批次未保存市場探索摘要，無法由入池數推算探索涵蓋率。</p>}
      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {Object.entries(stateLabels).map(([state, label]) => (
          <div key={state} className="rounded-[10px] border border-border-subtle p-3">
            <dt className="text-xs text-text-muted">{label}</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums text-text-primary">{summary.state_counts[state] ?? 0}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-text-muted">
        可比較未入選樣本 {summary.comparable_shadow_count} 檔；資格稽核樣本 {summary.eligibility_audit_shadow_count} 檔。
        未分類資料列 {summary.unclassified_record_count} 筆；重複資料列 {summary.duplicate_record_count} 筆。
      </p>
      {Object.keys(summary.reason_counts).length > 0 && (
        <details className="mt-3 border-t border-border-subtle pt-2">
          <summary className="min-h-10 cursor-pointer text-sm text-text-secondary">查看篩選原因</summary>
          <p className="mt-1 text-xs text-text-muted">同一標的可能同時有多個原因，原因數量不相加為排除總數。</p>
          <ul className="mt-2 grid gap-2 text-sm text-text-secondary sm:grid-cols-2">
            {Object.entries(summary.reason_counts).map(([reason, count]) => (
              <li key={reason}>{reasonLabels[reason] ?? "其他篩選原因"}：{count} 檔</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
