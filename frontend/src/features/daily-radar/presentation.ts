import { formatDataMissingReason } from "../../lib/presentationLabels";
import {
  DAILY_RADAR_BUCKETS,
  type DailyRadarBucket,
  type DailyRadarCandidate,
  type DailyRadarBackgroundContextLabel,
  type DailyRadarDateMap,
  type DailyRadarMatchedRule,
  type DailyRadarPhase1AvwapContext,
  type DailyRadarRepeatStatus,
  type DailyRadarRiskLabel,
  type DailyRadarRunStatus,
} from "../../lib/dailyRadarTypes";

export const BUCKET_LABEL: Record<DailyRadarBucket, string> = {
  institutional_accumulation: "法人籌碼延續",
  price_volume_strengthening: "量價結構轉強",
  bottoming_reversal: "低位修復",
  support_retest: "支撐回測",
};

const RISK_LABEL: Record<DailyRadarRiskLabel, string> = {
  overextended: "短線過熱",
  flow_conflict: "量價籌碼分歧",
  margin_crowding: "融資偏擁擠",
  market_weakness: "大盤偏弱",
  data_gap: "資料缺口",
};

const REPEAT_STATUS_LABEL: Record<DailyRadarRepeatStatus, string> = {
  new: "入選歷史待確認",
  repeat: "再次列入觀察",
  upgraded: "觀察強度提升",
  cooled_down: "觀察降溫追蹤",
};

const REPEAT_STATUS_CLASS: Record<DailyRadarRepeatStatus, string> = {
  new: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  repeat: "bg-badge-neutral-bg text-badge-neutral-text",
  upgraded: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  cooled_down: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
};

const BACKGROUND_CONTEXT_TYPE_LABEL: Record<string, string> = {
  weekly_major_holders: "大戶持股背景",
  lending: "借券背景",
  full_margin: "完整融資融券背景",
};

const BACKGROUND_CONTEXT_FRESHNESS_LABEL: Record<string, string> = {
  fresh: "資料可用",
  stale: "資料偏舊",
  missing: "資料缺口",
  unknown: "狀態未明",
};

const PHASE1_AVWAP_ANCHOR_ORDER = ["swing_low_60d", "breakout_20d", "high_volume_60d", "entry"] as const;

const PHASE1_AVWAP_ANCHOR_REFERENCE_LABEL: Record<string, string> = {
  swing_low_60d: "60 日低點 AVWAP",
  breakout_20d: "20 日突破 AVWAP",
  high_volume_60d: "60 日大量 AVWAP",
  entry: "持股進場日 AVWAP",
};

const RUN_STATUS_LABEL: Record<DailyRadarRunStatus, string> = {
  completed: "掃描完成",
  running: "掃描中",
  failed: "掃描未完成",
  stale_data: "資料需留意",
};

const RUN_STATUS_HELPER: Record<DailyRadarRunStatus, string> = {
  completed: "本次盤後雷達已完成",
  running: "本次盤後雷達仍在整理",
  failed: "本次掃描流程未完成",
  stale_data: "部分資料日期落後掃描日",
};

export function sortDailyRadarCandidates(candidates: DailyRadarCandidate[]): DailyRadarCandidate[] {
  return [...candidates].sort((a, b) => {
    const scoreDelta = b.observation_score - a.observation_score;
    if (scoreDelta !== 0) return scoreDelta;
    return a.symbol.localeCompare(b.symbol);
  });
}

export function getBucketCounts(candidates: DailyRadarCandidate[]): Record<DailyRadarBucket, number> {
  return DAILY_RADAR_BUCKETS.reduce(
    (counts, bucket) => ({
      ...counts,
      [bucket]: candidates.filter((candidate) => candidate.primary_bucket === bucket).length,
    }),
    {} as Record<DailyRadarBucket, number>,
  );
}

export function formatDate(value: string | null | undefined): string {
  return value || "—";
}

export function formatBucketLabel(value: string): string {
  return (BUCKET_LABEL as Record<string, string>)[value] ?? "其他觀察分類";
}

export function formatRiskLabel(value: string): string {
  return (RISK_LABEL as Record<string, string>)[value] ?? "其他風險訊號";
}

export function formatRepeatStatusLabel(value: string): string {
  return (REPEAT_STATUS_LABEL as Record<string, string>)[value] ?? "觀察狀態未明";
}

export function getObservationHistory(candidate: DailyRadarCandidate): Record<string, unknown> | null {
  const value = candidate.input_snapshot.observation_history;
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

export function formatMembershipLabel(candidate: DailyRadarCandidate): string {
  const history = getObservationHistory(candidate);
  switch (history?.membership_status) {
    case "new": return "可用紀錄首次列入";
    case "continuing": return "持續列入觀察";
    case "returning": return "重新列入觀察";
    default: return "入選歷史待確認";
  }
}

export function formatObservationHistory(candidate: DailyRadarCandidate): string | null {
  const history = getObservationHistory(candidate);
  if (!history) return null;
  const parts: string[] = [];
  if (typeof history.first_seen_date === "string") parts.push(`紀錄首次 ${history.first_seen_date}`);
  if (typeof history.last_seen_date === "string") parts.push(`上次 ${history.last_seen_date}`);
  if (typeof history.appearance_count === "number") parts.push(`累計 ${history.appearance_count} 次`);
  return parts.join("・") || null;
}

export function formatSignalStatus(candidate: DailyRadarCandidate): string | null {
  const labels: Record<string, string> = {
    improved: "觀察強度提升", stable: "觀察強度維持", cooled_down: "觀察強度降溫", unknown: "強度比較待確認",
  };
  const value = getObservationHistory(candidate)?.signal_status;
  return typeof value === "string" ? labels[value] ?? null : null;
}

export function getRepeatStatusClass(value: string): string {
  return (REPEAT_STATUS_CLASS as Record<string, string>)[value] ?? "bg-badge-neutral-bg text-badge-neutral-text";
}

export function formatRunStatusLabel(value: string): string {
  return (RUN_STATUS_LABEL as Record<string, string>)[value] ?? "掃描狀態未明";
}

export function formatRunStatusHelper(value: string): string {
  return (RUN_STATUS_HELPER as Record<string, string>)[value] ?? "請查看資料日期與流程紀錄確認狀態";
}

const DATA_SOURCE_LABEL: Record<string, string> = {
  ohlcv: "價格與成交量資料",
  technical_profile: "技術輪廓資料",
  technical_indicators: "技術指標資料",
  institutional_flow: "法人買賣超資料",
  margin: "融資融券資料",
  market_index: "大盤指數資料",
  background_context: "背景脈絡資料",
  relative_strength: "相對強弱資料",
  relative_strength_candidate: "個股相對強弱資料",
  relative_strength_benchmark: "基準指數相對強弱資料",
  daily_radar_universe: "雷達觀察名單來源",
};

export function formatDataSourceLabel(source: string): string {
  return DATA_SOURCE_LABEL[source] ?? "其他資料來源";
}

function isTraceRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDailyRadarPhase1AvwapContext(value: unknown): value is DailyRadarPhase1AvwapContext {
  return isTraceRecord(value) && isTraceRecord(value.anchors);
}

const TRACE_KEY_LABEL: Record<string, string> = {
  avg_volume_20: "20 日平均量",
  bucket_scores: "分類分數",
  close: "收盤價",
  components: "組成項目",
  consecutive_positive_days: "連續買超天數",
  cross_confirmation: "交叉確認",
  data_dates: "資料日期",
  data_quality: "資料品質",
  details: "細節",
  flow_state: "籌碼狀態",
  foreign_net_shares: "外資買賣超股數",
  freshness: "資料新鮮度",
  background_context: "背景脈絡",
  background_context_labels: "背景脈絡標籤",
  high: "最高價",
  indicators: "技術指標",
  institutional_flow: "法人買賣超資料",
  investment_trust_net_shares: "投信買賣超股數",
  label: "標籤",
  low: "最低價",
  ma5: "5 日均線（MA5）",
  ma20: "20 日均線（MA20）",
  ma60: "60 日均線（MA60）",
  margin: "融資融券資料",
  margin_delta_pct: "融資餘額變化率",
  margin_to_volume: "融資量能比",
  market_context: "大盤環境",
  net_flow_to_avg_volume: "法人淨流量／均量",
  ohlcv: "價格與成交量資料",
  open: "開盤價",
  previous_close: "前一交易日收盤",
  primary_bucket_score: "主要分類原始分",
  resistance_level: "壓力價位",
  risk_adjustment: "風險調整",
  risk_penalties: "風險扣分",
  rsi14: "14 日相對強弱指標（RSI）",
  score: "分數",
  source_provider: "資料來源",
  support_level: "支撐價位",
  three_party_net_shares: "三大法人買賣超股數",
  volatility_state: "波動狀態",
  volume: "成交量",
  weighted_primary_bucket_score: "主要分類加權分",
  atr14: "14 日平均真實波幅（ATR）",
  mfi14: "14 日資金流量指標（MFI）",
  obv_trend: "能量潮趨勢（OBV）",
  macd_histogram: "指數平滑異同移動平均柱狀體（MACD）",
  kd_k: "KD 隨機指標 K 值",
  kd_d: "KD 隨機指標 D 值",
};

export function formatTraceKey(value: string): string {
  return TRACE_KEY_LABEL[value] ?? "其他追蹤欄位";
}

const MATCHED_RULE_DETAIL_LABEL: Record<string, string> = {
  above_ma20: "站上 20 日均線（MA20）",
  above_ma60: "站上 60 日均線（MA60）",
  atr14: "14 日平均真實波幅（ATR）",
  avg_volume_20: "20 日平均量",
  bias20: "20 日乖離率",
  close: "收盤價",
  consecutive_buy_days: "連續買超天數",
  consecutive_negative_days: "連續賣超天數",
  consecutive_positive_days: "連續買超天數",
  context_flags: "情境旗標",
  cumulative_net_buy: "累計買超",
  data_dates: "資料日期",
  days: "連續天數",
  details: "細節",
  flow_state: "籌碼狀態",
  foreign_consecutive_buy_days: "外資連續買超日數",
  foreign_cumulative_net: "外資近期累計買超",
  foreign_cumulative_net_shares: "外資近期累計買超股數",
  foreign_latest_net_shares: "外資最新買賣超股數",
  foreign_net: "外資買賣超",
  foreign_net_shares: "外資買賣超股數",
  foreign_same_day_net_shares: "外資當日買超股數",
  high: "最高價",
  institutional_flow: "法人籌碼",
  institutional_universe_tracks: "法人篩選軌道",
  investment_trust_net: "投信買賣超",
  investment_trust_net_shares: "投信買賣超股數",
  kd_d: "KD 隨機指標 D 值",
  kd_k: "KD 隨機指標 K 值",
  label: "標籤",
  low: "最低價",
  ma5: "5 日均線（MA5）",
  ma20: "20 日均線（MA20）",
  ma60: "60 日均線（MA60）",
  macd_histogram: "指數平滑異同移動平均柱狀體（MACD）",
  margin_delta_pct: "融資餘額變化率",
  margin_to_volume: "融資量能比",
  market: "大盤背景",
  market_risk_flags: "大盤風險旗標",
  mfi14: "14 日資金流量指標（MFI）",
  missing_trading_days_60: "近 60 日缺漏交易日",
  net_flow_to_avg_volume: "法人淨流量 / 均量",
  obv_trend: "能量潮趨勢（OBV）",
  ohlcv: "價格量能",
  open: "開盤價",
  previous_close: "前一交易日收盤",
  reason: "原因",
  recent_accumulation_rank: "近期累積排名",
  recent_actor: "近期主力法人",
  recent_concentration: "近期買超集中度",
  recent_source_dates: "近期資料來源日期",
  resistance: "壓力價位",
  resistance_level: "壓力價位",
  risk_flags: "風險旗標",
  rsi14: "14 日相對強弱指標（RSI）",
  same_day_actor: "當日主力法人",
  same_day_concentration: "當日買超集中度",
  same_day_net_buy: "當日買超",
  same_day_rank: "當日法人排名",
  same_day_source_dates: "當日資料來源日期",
  score: "分數",
  score_adjustment: "分數調整",
  scores: "分數",
  source_provider: "資料來源",
  support: "支撐價位",
  support_level: "支撐價位",
  symbol_overrides: "個股情境覆寫",
  technical_indicators: "技術指標",
  technical_profile: "技術輪廓",
  three_party_net: "三大法人買賣超",
  three_party_net_shares: "三大法人買賣超股數",
  trust_consecutive_buy_days: "投信連續買超日數",
  trust_cumulative_net: "投信近期累計買超",
  trust_cumulative_net_shares: "投信近期累計買超股數",
  trust_latest_net_shares: "投信最新買賣超股數",
  trust_same_day_net_shares: "投信當日買超股數",
  volatility_state: "波動狀態",
  volume: "成交量",
  volume_ratio: "量能倍數",
};

const MATCHED_RULE_VALUE_LABEL: Record<string, string> = {
  bottoming_reversal: "低位修復",
  conflict: "法人方向分歧",
  consistent_accumulation: "連續累積",
  data_gap: "資料缺口",
  early_stabilization: "初步止穩",
  elevated: "波動偏高",
  falling: "轉弱下滑",
  flat: "持平",
  flat_to_up: "由平轉升",
  flow_conflict: "量價籌碼分歧",
  foreign: "外資",
  fresh: "資料新鮮",
  high: "高",
  institutional: "三大法人",
  institutional_accumulation: "法人籌碼延續",
  institutional_flow: "法人籌碼",
  investment_trust: "投信",
  foreign_recent_accumulation: "外資近期連續買超",
  foreign_same_day: "外資當日買超",
  late_momentum: "動能偏晚",
  margin_crowding: "融資偏擁擠",
  market_weakness: "大盤偏弱",
  neutral: "中性",
  normal: "正常",
  overextended: "短線過熱",
  market_trend: "全市場中期趨勢探索",
  market_price_volume: "全市場量價探索",
  price_volume: "量價結構",
  price_volume_strengthening: "量價結構轉強",
  recent_accumulation: "近期累積買超",
  rising: "走升",
  rising_fast: "快速走升",
  same_day_institutional: "當日法人買超",
  same_day_net_buy: "當日買超",
  stable: "穩定",
  stale_core_data: "核心資料落後",
  stale_data: "資料落後",
  support_area_accumulation: "支撐區承接累積",
  supportive: "支持觀察",
  support_retest: "支撐回測",
  technical: "技術面",
  trust: "投信",
  trust_recent_accumulation: "投信近期連續買超",
  trust_same_day: "投信當日買超",
  turning_up: "轉強",
  volume_confirmed_accumulation: "量能確認累積",
  weak: "偏弱",
  weak_confirmation: "確認偏弱",
};

export function formatBackgroundContextType(value: string): string {
  return BACKGROUND_CONTEXT_TYPE_LABEL[value] ?? "其他背景資料";
}

export function formatBackgroundFreshness(value: string): string {
  return BACKGROUND_CONTEXT_FRESHNESS_LABEL[value] ?? "狀態未明";
}

export function formatPhase1AvwapFreshness(value: string): string {
  return BACKGROUND_CONTEXT_FRESHNESS_LABEL[value] ?? "狀態未明";
}

export function formatPhase1AvwapMissingReason(reason: string | null | undefined): string {
  return formatDataMissingReason(reason, "AVWAP 資料不足");
}

export function backgroundLabelClass(label: DailyRadarBackgroundContextLabel): string {
  if (label.freshness === "fresh")
    return "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200";
  if (label.freshness === "stale")
    return "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200";
  return "border-border-subtle bg-surface text-text-secondary";
}

export function getBucketResearchThesis(bucket: DailyRadarBucket): string {
  switch (bucket) {
    case "institutional_accumulation":
      return "法人籌碼延續候選，重點是確認買盤是否延續，且價格沒有過熱或籌碼分歧。";
    case "price_volume_strengthening":
      return "量價結構轉強候選，重點是確認突破或轉強後，成交量與收盤位置能否維持。";
    case "bottoming_reversal":
      return "低位修復候選，重點是確認低點不再破壞，技術與籌碼是否同步改善。";
    case "support_retest":
      return "支撐回測候選，重點是確認回測後支撐是否繼續有效。";
  }
}

export function getBucketInvalidationHint(bucket: DailyRadarBucket): string {
  switch (bucket) {
    case "institutional_accumulation":
      return "若法人買盤中斷、收盤轉弱或融資同步升溫，這個觀察理由會減弱。";
    case "price_volume_strengthening":
      return "若轉強後跌回整理區、放量收弱或動能過熱，這個觀察理由會減弱。";
    case "bottoming_reversal":
      return "若再破近期低點、均線繼續下彎或量能無法配合，這個觀察理由會減弱。";
    case "support_retest":
      return "若跌破支撐、跌離 MA20/MA60，或回測後量能與 OBV 轉弱，這個觀察理由會減弱。";
  }
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatMetric(value: unknown, digits = 2): string | null {
  const numberValue = toFiniteNumber(value);
  if (numberValue === null) return null;
  return numberValue.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: Number.isInteger(numberValue) ? 0 : Math.min(2, digits),
  });
}

export function formatSignedPct(value: unknown, digits = 2): string {
  const numberValue = toFiniteNumber(value);
  if (numberValue === null) return "—";
  const formatted = numberValue.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: Number.isInteger(numberValue) ? 0 : Math.min(2, digits),
  });
  return `${numberValue > 0 ? "+" : ""}${formatted}%`;
}

function getRuleNumber(rule: DailyRadarMatchedRule, key: string): string | null {
  return formatMetric(rule.details[key]);
}

function getMatchedRuleSummary(rule: DailyRadarMatchedRule): string {
  switch (rule.rule_id) {
    case "support_retest_reclaimed_area": {
      const close = getRuleNumber(rule, "close");
      const support = getRuleNumber(rule, "support_level");
      const previousClose = getRuleNumber(rule, "previous_close");
      if (close && support && previousClose) return `收盤 ${close} 收復支撐 ${support}，前收 ${previousClose}。`;
      return "收盤重新站回支撐區。";
    }
    case "support_retest_ma20_area": {
      const close = getRuleNumber(rule, "close");
      const ma20 = getRuleNumber(rule, "ma20");
      if (close && ma20) return `收盤 ${close} 接近 MA20 ${ma20}。`;
      return "收盤貼近 MA20。";
    }
    case "support_retest_ma60_area": {
      const close = getRuleNumber(rule, "close");
      const ma60 = getRuleNumber(rule, "ma60");
      if (close && ma60) return `收盤 ${close} 接近 MA60 ${ma60}。`;
      return "收盤貼近 MA60。";
    }
    case "support_retest_atr_contained": {
      const atr14 = getRuleNumber(rule, "atr14");
      if (atr14) return `ATR ${atr14}，回測波動仍在可控範圍。`;
      return "ATR 顯示回測波動仍可控。";
    }
    case "support_retest_participation_stable":
      return `OBV ${formatMatchedRuleValue(String(rule.details.obv_trend ?? "")) || "未再轉弱"}，量能參與未明顯惡化。`;
    case "support_retest_margin_not_expanding": {
      const marginDelta = getRuleNumber(rule, "margin_delta_pct");
      if (marginDelta) return `融資變化率 ${marginDelta}，沒有同步擴張。`;
      return "融資沒有同步擴張。";
    }
    case "support_retest_macd_stable":
      return "MACD 柱狀體沒有明顯轉弱。";
    default:
      return rule.label;
  }
}

export function getCandidateReasonHighlights(candidate: DailyRadarCandidate): string[] {
  const summaries = candidate.matched_rules.map(getMatchedRuleSummary);
  return Array.from(new Set(summaries)).slice(0, 3);
}

function findRule(candidate: DailyRadarCandidate, ruleId: string): DailyRadarMatchedRule | undefined {
  return candidate.matched_rules.find((rule) => rule.rule_id === ruleId);
}

export function getCandidateWatchItems(candidate: DailyRadarCandidate): string[] {
  if (candidate.primary_bucket === "support_retest") {
    const reclaimedRule = findRule(candidate, "support_retest_reclaimed_area");
    const ma20Rule = findRule(candidate, "support_retest_ma20_area");
    const ma60Rule = findRule(candidate, "support_retest_ma60_area");
    const support = reclaimedRule ? getRuleNumber(reclaimedRule, "support_level") : null;
    const ma20 = ma20Rule ? getRuleNumber(ma20Rule, "ma20") : null;
    const ma60 = ma60Rule ? getRuleNumber(ma60Rule, "ma60") : null;

    return [
      support ? `支撐 ${support} 是否守住。` : "回測支撐區是否守住。",
      ma20 || ma60
        ? `收盤是否維持在 ${[ma20 ? `MA20 ${ma20}` : null, ma60 ? `MA60 ${ma60}` : null].filter(Boolean).join("、")} 附近。`
        : "收盤是否維持在關鍵均線附近。",
      "量能、OBV 與融資是否維持穩定，不要同步轉弱或升溫。",
    ];
  }

  if (candidate.primary_bucket === "institutional_accumulation") {
    return [
      "法人買盤是否延續，尤其是主要買方是否連續承接。",
      "價格是否維持在關鍵均線附近，不要出現買超但收弱。",
      "融資是否避免同步快速擴張。",
    ];
  }

  if (candidate.primary_bucket === "price_volume_strengthening") {
    return ["轉強後是否能守住突破區或整理區上緣。", "成交量是否維持健康放大，不要爆量收弱。", "短線過熱風險是否受控。"];
  }

  return ["近期低點是否不再被跌破。", "MACD、KD 或量能是否繼續改善。", "反彈是否有法人或量價結構配合。"];
}

export function getBackgroundContextUse(label: DailyRadarBackgroundContextLabel): string {
  if (label.freshness === "missing") {
    return "這次沒有可用資料，只保留資料缺口，不影響排序。";
  }
  if (label.freshness === "stale") {
    return "可作為背景參考，但日期偏舊，不能當成最新訊號。";
  }

  switch (label.context_type) {
    case "weekly_major_holders":
      return "用來判斷持股集中度是否支撐籌碼穩定，屬於週頻背景。";
    case "lending":
      return "用來確認借券空方壓力是否擴大或降溫。";
    case "full_margin":
      return "用來確認融資是否同步擴張、券資是否出現擁擠。";
    default:
      return "用來補充背景脈絡與資料品質，不參與每日排序。";
  }
}

export function getCandidateDisplayName(candidate: DailyRadarCandidate): string | null {
  const name = candidate.name.trim();
  return name && name !== candidate.symbol ? name : null;
}

export function getCandidateDisplayTitle(candidate: DailyRadarCandidate): string {
  const displayName = getCandidateDisplayName(candidate);
  return displayName ? `${displayName} · ${candidate.symbol}` : candidate.symbol;
}

export function formatMatchedRuleDetailKey(value: string): string {
  return (
    MATCHED_RULE_DETAIL_LABEL[value] ??
    BUCKET_LABEL[value as DailyRadarBucket] ??
    RISK_LABEL[value as DailyRadarRiskLabel] ??
    formatTraceKey(value)
  );
}

export function formatMatchedRuleValue(value: string): string {
  return (
    MATCHED_RULE_VALUE_LABEL[value] ??
    BUCKET_LABEL[value as DailyRadarBucket] ??
    RISK_LABEL[value as DailyRadarRiskLabel] ??
    "其他狀態"
  );
}

export function formatTraceValue(
  value: unknown,
  formatKey: (key: string) => string = formatTraceKey,
  formatStringValue: (value: string) => string = (text) => text,
): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString() : String(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") {
    const formatted = formatStringValue(value);
    return formatted.length > 96 ? `${formatted.slice(0, 96)}…` : formatted;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    const preview = value
      .slice(0, 4)
      .map((item) => formatTraceValue(item, formatKey, formatStringValue))
      .join("、");
    return value.length > 4 ? `${preview}，另 ${value.length - 4} 項` : preview;
  }
  if (isTraceRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return "—";
    const preview = entries
      .slice(0, 3)
      .map(([key, nestedValue]) => `${formatKey(key)}：${formatTraceValue(nestedValue, formatKey, formatStringValue)}`)
      .join("；");
    return entries.length > 3 ? `${preview}；另 ${entries.length - 3} 項` : preview;
  }
  return String(value);
}

const FOCUSABLE_ELEMENT_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function getActiveHTMLElement(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

export function isFocusableElement(element: HTMLElement | null): element is HTMLElement {
  if (!element?.isConnected) return false;
  if (element.matches("[disabled], [aria-disabled='true']")) return false;

  if (typeof window !== "undefined") {
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
  }

  return element.matches(FOCUSABLE_ELEMENT_SELECTOR);
}

export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_ELEMENT_SELECTOR)).filter(isFocusableElement);
}

export function getFreshnessSummary(runDate: string | null | undefined, dataDates: DailyRadarDateMap): string {
  const dates = Object.values(dataDates).filter(Boolean);
  if (dates.length === 0) return "資料日期尚未回傳，請留意後續同步狀態。";

  const latestDate = dates.reduce((latest, date) => (date > latest ? date : latest), dates[0]);
  if (!runDate) return `資料最新日期 ${latestDate}。`;

  const laggingCount = dates.filter((date) => date < runDate).length;
  if (laggingCount === 0) return "資料日期與最新掃描日一致。";

  return `資料最新日期 ${latestDate}，${laggingCount} 項資料早於掃描日。`;
}

export function hasLaggingRunData(runDate: string | null | undefined, dataDates: DailyRadarDateMap): boolean {
  if (!runDate) return false;
  return Object.values(dataDates).some((date) => Boolean(date) && date < runDate);
}

export function getRunStatusClass(status: DailyRadarRunStatus): string {
  if (status === "completed") return "text-positive";
  if (status === "stale_data") return "text-signal";
  if (status === "failed") return "text-negative";
  return "text-text-primary";
}

export function getPhase1AvwapContext(candidate: DailyRadarCandidate): DailyRadarPhase1AvwapContext | null {
  const context = candidate.input_snapshot.phase1_avwap_context;
  return isDailyRadarPhase1AvwapContext(context) ? context : null;
}

export function getPhase1AvwapDisplayAnchors(context: DailyRadarPhase1AvwapContext): Array<{
  key: string;
  referenceLabel: string;
  avwap?: number | null;
  distance?: number | null;
  anchorDate?: string | null;
  estimated?: boolean;
}> {
  const entries = Object.entries(context.anchors ?? {}).filter(([, anchor]) => anchor.available !== false);
  const priority: Map<string, number> = new Map(PHASE1_AVWAP_ANCHOR_ORDER.map((key, index) => [key, index]));

  return entries
    .sort(([left], [right]) => (priority.get(left) ?? 99) - (priority.get(right) ?? 99) || left.localeCompare(right))
    .slice(0, 4)
    .map(([key, anchor]) => ({
      key,
      referenceLabel: PHASE1_AVWAP_ANCHOR_REFERENCE_LABEL[key] ?? "其他 AVWAP 觀察線",
      avwap: anchor.avwap,
      distance: anchor.distance_to_avwap_pct,
      anchorDate: anchor.anchor_date,
      estimated: anchor.estimated,
    }));
}

export function formatPhase1AvwapDistanceLine(anchor: { distance?: number | null; referenceLabel: string }): string {
  const distance = toFiniteNumber(anchor.distance);
  if (distance === null) return `資料日價格相對 ${anchor.referenceLabel}`;
  if (distance > 0) return `資料日價格高於 ${anchor.referenceLabel}`;
  if (distance < 0) return `資料日價格低於 ${anchor.referenceLabel}`;
  return `資料日價格貼近 ${anchor.referenceLabel}`;
}
