import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchLatestDailyRadarRun,
  isNoPublicDailyRadarRunUnavailableError,
  toDailyRadarDisplayError,
  type DailyRadarDisplayError,
} from "../lib/dailyRadarApi";
import { createWatchlistItem, fetchWatchlistItems } from "../lib/watchlistApi";
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
  type DailyRadarRunResponse,
  type DailyRadarRunStatus,
} from "../lib/dailyRadarTypes";

const BUCKET_LABEL: Record<DailyRadarBucket, string> = {
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
  new: "首次列入觀察",
  repeat: "連續觀察中",
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

const PHASE1_AVWAP_ANCHOR_LABEL: Record<string, string> = {
  swing_low_60d: "60 日波段低點",
  breakout_20d: "20 日突破",
  high_volume_60d: "60 日大量",
  entry: "持股進場日",
};

const PHASE1_AVWAP_MISSING_REASON_LABEL: Record<string, string> = {
  phase1_snapshot_missing: "尚無試驗版快照",
  phase1_snapshot_stale: "試驗版快照已過期",
  phase1_snapshot_read_failed: "試驗版快照讀取失敗",
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

type WatchlistAddStatus = "idle" | "saving" | "success" | "error";

function sortDailyRadarCandidates(candidates: DailyRadarCandidate[]): DailyRadarCandidate[] {
  return [...candidates].sort((a, b) => {
    const scoreDelta = b.observation_score - a.observation_score;
    if (scoreDelta !== 0) return scoreDelta;
    return a.symbol.localeCompare(b.symbol);
  });
}

function getBucketCounts(candidates: DailyRadarCandidate[]): Record<DailyRadarBucket, number> {
  return DAILY_RADAR_BUCKETS.reduce(
    (counts, bucket) => ({
      ...counts,
      [bucket]: candidates.filter((candidate) => candidate.primary_bucket === bucket).length,
    }),
    {} as Record<DailyRadarBucket, number>,
  );
}

function formatDate(value: string | null | undefined): string {
  return value || "—";
}

const DATA_SOURCE_LABEL: Record<string, string> = {
  ohlcv: "價格與成交量資料",
  technical_indicators: "技術指標資料",
  institutional_flow: "法人買賣超資料",
  margin: "融資融券資料",
  market_index: "大盤指數資料",
  daily_radar_universe: "雷達觀察名單來源",
};

function formatDataSourceLabel(source: string): string {
  return DATA_SOURCE_LABEL[source] ?? `其他資料來源（${source}）`;
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

function formatTraceKey(value: string): string {
  return TRACE_KEY_LABEL[value] ?? `其他追蹤欄位（${value}）`;
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
  foreign_net: "外資買賣超",
  foreign_net_shares: "外資買賣超股數",
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
  three_party_net: "三大法人買賣超",
  three_party_net_shares: "三大法人買賣超股數",
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
  late_momentum: "動能偏晚",
  margin_crowding: "融資偏擁擠",
  market_weakness: "大盤偏弱",
  neutral: "中性",
  normal: "正常",
  overextended: "短線過熱",
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
  turning_up: "轉強",
  volume_confirmed_accumulation: "量能確認累積",
  weak: "偏弱",
  weak_confirmation: "確認偏弱",
};

function formatBackgroundContextType(value: string): string {
  return BACKGROUND_CONTEXT_TYPE_LABEL[value] ?? formatTraceKey(value);
}

function formatBackgroundFreshness(value: string): string {
  return BACKGROUND_CONTEXT_FRESHNESS_LABEL[value] ?? formatMatchedRuleValue(value);
}

function formatPhase1AvwapFreshness(value: string): string {
  return BACKGROUND_CONTEXT_FRESHNESS_LABEL[value] ?? formatMatchedRuleValue(value);
}

function formatPhase1AvwapMissingReason(reason: string | null | undefined): string {
  if (!reason) return "資料不足";
  return PHASE1_AVWAP_MISSING_REASON_LABEL[reason] ?? reason;
}

function backgroundLabelClass(label: DailyRadarBackgroundContextLabel): string {
  if (label.freshness === "fresh")
    return "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200";
  if (label.freshness === "stale")
    return "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200";
  return "border-border-subtle bg-surface text-text-secondary";
}

function getBucketResearchThesis(bucket: DailyRadarBucket): string {
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

function getBucketInvalidationHint(bucket: DailyRadarBucket): string {
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

function formatMetric(value: unknown, digits = 2): string | null {
  const numberValue = toFiniteNumber(value);
  if (numberValue === null) return null;
  return numberValue.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: Number.isInteger(numberValue) ? 0 : Math.min(2, digits),
  });
}

function formatSignedPct(value: unknown, digits = 2): string {
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

function getCandidateReasonHighlights(candidate: DailyRadarCandidate): string[] {
  const summaries = candidate.matched_rules.map(getMatchedRuleSummary);
  return Array.from(new Set(summaries)).slice(0, 3);
}

function findRule(candidate: DailyRadarCandidate, ruleId: string): DailyRadarMatchedRule | undefined {
  return candidate.matched_rules.find((rule) => rule.rule_id === ruleId);
}

function getCandidateWatchItems(candidate: DailyRadarCandidate): string[] {
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

function getBackgroundContextUse(label: DailyRadarBackgroundContextLabel): string {
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

function getCandidateDisplayName(candidate: DailyRadarCandidate): string | null {
  const name = candidate.name.trim();
  return name && name !== candidate.symbol ? name : null;
}

function getCandidateDisplayTitle(candidate: DailyRadarCandidate): string {
  const displayName = getCandidateDisplayName(candidate);
  return displayName ? `${candidate.symbol} · ${displayName}` : candidate.symbol;
}

function normalizeSymbol(symbol: string): string {
  return symbol.trim().toUpperCase();
}

function formatMatchedRuleDetailKey(value: string): string {
  return (
    MATCHED_RULE_DETAIL_LABEL[value] ??
    BUCKET_LABEL[value as DailyRadarBucket] ??
    RISK_LABEL[value as DailyRadarRiskLabel] ??
    formatTraceKey(value)
  );
}

function formatMatchedRuleValue(value: string): string {
  return (
    MATCHED_RULE_VALUE_LABEL[value] ??
    BUCKET_LABEL[value as DailyRadarBucket] ??
    RISK_LABEL[value as DailyRadarRiskLabel] ??
    value
  );
}

function formatTraceValue(
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

function getActiveHTMLElement(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.activeElement instanceof HTMLElement ? document.activeElement : null;
}

function isFocusableElement(element: HTMLElement | null): element is HTMLElement {
  if (!element?.isConnected) return false;
  if (element.matches("[disabled], [aria-disabled='true']")) return false;

  if (typeof window !== "undefined") {
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
  }

  return element.matches(FOCUSABLE_ELEMENT_SELECTOR);
}

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_ELEMENT_SELECTOR)).filter(isFocusableElement);
}

function getFreshnessSummary(runDate: string | null | undefined, dataDates: DailyRadarDateMap): string {
  const dates = Object.values(dataDates).filter(Boolean);
  if (dates.length === 0) return "資料日期尚未回傳，請留意後續同步狀態。";

  const latestDate = dates.reduce((latest, date) => (date > latest ? date : latest), dates[0]);
  if (!runDate) return `資料最新日期 ${latestDate}。`;

  const laggingCount = dates.filter((date) => date < runDate).length;
  if (laggingCount === 0) return "資料日期與最新掃描日一致。";

  return `資料最新日期 ${latestDate}，${laggingCount} 項資料早於掃描日。`;
}

function hasLaggingRunData(runDate: string | null | undefined, dataDates: DailyRadarDateMap): boolean {
  if (!runDate) return false;
  return Object.values(dataDates).some((date) => Boolean(date) && date < runDate);
}

function StatCard({ label, value, helper }: { label: string; value: string; helper?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs font-medium text-text-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-text-primary">{value}</p>
      {helper && <p className="mt-1 text-xs text-text-faint">{helper}</p>}
    </div>
  );
}

function DailyRadarBucketTabs({
  selectedBucket,
  counts,
  totalCount,
  onSelectBucket,
}: {
  selectedBucket: DailyRadarBucket | null;
  counts: Record<DailyRadarBucket, number>;
  totalCount: number;
  onSelectBucket: (bucket: DailyRadarBucket | null) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3 shadow-sm">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="候選觀察分類">
        <button
          type="button"
          role="tab"
          aria-selected={selectedBucket === null}
          onClick={() => onSelectBucket(null)}
          className={`rounded-lg px-3 py-2 text-left text-sm font-medium transition ${
            selectedBucket === null
              ? "bg-indigo-600 text-white shadow-sm"
              : "border border-border bg-card text-text-muted hover:bg-card-hover hover:text-text-secondary"
          }`}
        >
          <span>全部候選</span>
          <span className="ml-2 rounded-full bg-white/20 px-2 py-0.5 text-xs">{totalCount}</span>
        </button>
        {DAILY_RADAR_BUCKETS.map((bucket) => {
          const active = selectedBucket === bucket;
          return (
            <button
              key={bucket}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onSelectBucket(bucket)}
              className={`rounded-lg px-3 py-2 text-left text-sm font-medium transition ${
                active
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "border border-border bg-card text-text-muted hover:bg-card-hover hover:text-text-secondary"
              }`}
            >
              <span>{BUCKET_LABEL[bucket]}</span>
              <span
                className={`ml-2 rounded-full px-2 py-0.5 text-xs ${active ? "bg-white/20" : "bg-badge-neutral-bg text-badge-neutral-text"}`}
              >
                {counts[bucket]}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TraceValueList({
  payload,
  emptyText,
  formatKey = formatTraceKey,
  formatValue,
}: {
  payload: Record<string, unknown>;
  emptyText: string;
  formatKey?: (key: string) => string;
  formatValue?: (value: string) => string;
}) {
  const entries = Object.entries(payload).filter(([key]) => key !== "observation_score");

  if (entries.length === 0) {
    return (
      <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">{emptyText}</p>
    );
  }

  return (
    <dl className="grid gap-2 md:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
          <dt className="text-xs font-medium text-text-muted">{formatKey(key)}</dt>
          <dd className="mt-1 break-words text-sm text-text-primary">
            {formatTraceValue(value, formatKey, formatValue)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function BackgroundContextLabels({ labels }: { labels: DailyRadarBackgroundContextLabel[] }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-text-primary">背景脈絡</h3>
      <p className="mt-1 text-xs leading-relaxed text-text-muted">
        這些資料只用來補充研究背景與資料品質，不改變分類、分數或排序。
      </p>
      <div className="mt-3 grid gap-2">
        {labels.length > 0 ? (
          labels.map((label) => (
            <article
              key={`${label.context_type}:${label.replay_key}`}
              className={`rounded-lg border px-3 py-3 ${backgroundLabelClass(label)}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-current">
                    {formatBackgroundContextType(label.context_type)}
                  </p>
                  <p className="mt-1 text-xs opacity-75">{label.label}</p>
                </div>
                <span className="rounded-md bg-white/60 px-2 py-0.5 text-xs font-medium text-current dark:bg-white/10">
                  {formatBackgroundFreshness(label.freshness)}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-current">{getBackgroundContextUse(label)}</p>
              <p className="mt-2 text-xs opacity-70">
                資料日期：{formatDate(label.as_of_date)}
                {label.missing_reason ? `，缺資料原因：${label.missing_reason}` : ""}
              </p>
            </article>
          ))
        ) : (
          <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
            尚未回傳背景脈絡標籤。
          </p>
        )}
      </div>
    </section>
  );
}

function getPhase1AvwapContext(candidate: DailyRadarCandidate): DailyRadarPhase1AvwapContext | null {
  const context = candidate.input_snapshot.phase1_avwap_context;
  return isDailyRadarPhase1AvwapContext(context) ? context : null;
}

function getPhase1AvwapDisplayAnchors(context: DailyRadarPhase1AvwapContext): Array<{
  key: string;
  label: string;
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
      label: PHASE1_AVWAP_ANCHOR_LABEL[key] ?? formatTraceKey(key),
      avwap: anchor.avwap,
      distance: anchor.distance_to_avwap_pct,
      anchorDate: anchor.anchor_date,
      estimated: anchor.estimated,
    }));
}

function Phase1AvwapContextPanel({ candidate }: { candidate: DailyRadarCandidate }) {
  const context = getPhase1AvwapContext(candidate);
  const anchors = context ? getPhase1AvwapDisplayAnchors(context) : [];
  const isMissing = !context || context.freshness === "missing" || Boolean(context.missing_reason);

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">試驗版 AVWAP 脈絡</h3>
          <p className="mt-1 text-xs leading-relaxed text-text-muted">
            只作為明細脈絡與資料品質參考，不改變 Daily Radar 排序、分類、分數或風險標籤。
          </p>
        </div>
        <span
          className={`rounded-md border px-2 py-0.5 text-xs font-medium ${
            isMissing
              ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
              : "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
          }`}
        >
          {context ? formatPhase1AvwapFreshness(context.freshness) : "尚未回傳"}
        </span>
      </div>

      {context ? (
        <div className="mt-4 space-y-3">
          <div className="grid gap-2 md:grid-cols-3">
            <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
              <p className="text-xs font-medium text-text-muted">資料日期</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(context.data_date)}</p>
            </div>
            <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
              <p className="text-xs font-medium text-text-muted">資料集</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">{context.dataset}</p>
            </div>
            <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
              <p className="text-xs font-medium text-text-muted">調整模式</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">{context.adjustment_mode}</p>
            </div>
          </div>

          {anchors.length > 0 ? (
            <div className="grid gap-2 md:grid-cols-2">
              {anchors.map((anchor) => (
                <article key={anchor.key} className="rounded-lg border border-border-subtle bg-surface px-3 py-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="text-xs font-semibold text-text-muted">{anchor.label}</p>
                      {anchor.anchorDate && <p className="mt-1 text-xs text-text-faint">{anchor.anchorDate}</p>}
                    </div>
                    {anchor.estimated && (
                      <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                        日資料估算
                      </span>
                    )}
                  </div>
                  <div className="mt-3 flex flex-wrap items-end gap-3">
                    <p className="font-mono text-lg font-semibold text-text-primary">
                      {formatMetric(anchor.avwap) ?? "—"}
                    </p>
                    <p
                      className={`font-mono text-sm font-semibold ${
                        anchor.distance == null
                          ? "text-text-faint"
                          : anchor.distance >= 0
                            ? "text-emerald-600 dark:text-emerald-300"
                            : "text-red-600 dark:text-red-300"
                      }`}
                    >
                      {formatSignedPct(anchor.distance)}
                    </p>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-muted">
              {formatPhase1AvwapMissingReason(context.missing_reason)}
            </p>
          )}
        </div>
      ) : (
        <p className="mt-3 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
          這筆候選尚未包含試驗版 AVWAP 脈絡。
        </p>
      )}
    </section>
  );
}

function CandidateResearchCard({ candidate }: { candidate: DailyRadarCandidate }) {
  const reasons = getCandidateReasonHighlights(candidate);
  const watchItems = getCandidateWatchItems(candidate);

  return (
    <section className="rounded-xl border border-indigo-500/30 bg-indigo-50/60 p-4 shadow-sm dark:border-indigo-400/20 dark:bg-indigo-950/20">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${REPEAT_STATUS_CLASS[candidate.repeat_status]}`}
        >
          {REPEAT_STATUS_LABEL[candidate.repeat_status]}
        </span>
        <span className="rounded-md bg-white/70 px-2 py-0.5 text-xs font-medium text-indigo-800 dark:bg-white/10 dark:text-indigo-200">
          {BUCKET_LABEL[candidate.primary_bucket]}候選
        </span>
      </div>
      <h3 className="mt-3 text-base font-semibold text-text-primary">
        {BUCKET_LABEL[candidate.primary_bucket]}候選，僅供觀察追蹤
      </h3>
      <p className="mt-2 text-sm leading-relaxed text-text-secondary">
        {getBucketResearchThesis(candidate.primary_bucket)}
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-border-subtle bg-card/80 px-3 py-3">
          <p className="text-xs font-semibold text-text-muted">本次入選原因</p>
          {reasons.length > 0 ? (
            <ul className="mt-2 space-y-2 text-sm leading-relaxed text-text-primary">
              {reasons.map((reason) => (
                <li key={reason} className="flex gap-2">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-500" aria-hidden="true" />
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-text-faint">尚未回傳可讀的入選原因。</p>
          )}
        </div>
        <div className="rounded-lg border border-border-subtle bg-card/80 px-3 py-3">
          <p className="text-xs font-semibold text-text-muted">隔日觀察點</p>
          <ul className="mt-2 space-y-2 text-sm leading-relaxed text-text-primary">
            {watchItems.map((item) => (
              <li key={item} className="flex gap-2">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden="true" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-border-subtle bg-card/80 px-3 py-3">
        <p className="text-xs font-semibold text-text-muted">失效條件</p>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">
          {getBucketInvalidationHint(candidate.primary_bucket)}
        </p>
      </div>
    </section>
  );
}

function TechnicalTraceDetails({
  candidate,
  dataDateEntries,
}: {
  candidate: DailyRadarCandidate;
  dataDateEntries: [string, string][];
}) {
  return (
    <details className="rounded-xl border border-border bg-card p-4">
      <summary className="cursor-pointer text-sm font-semibold text-text-primary focus:outline-none focus:ring-2 focus:ring-indigo-400">
        技術細節 / Debug trace
      </summary>
      <div className="mt-4 space-y-5">
        {candidate.explanation && (
          <section>
            <h4 className="text-sm font-semibold text-text-primary">原始後端說明</h4>
            <p className="mt-2 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm leading-relaxed text-text-secondary">
              {candidate.explanation}
            </p>
          </section>
        )}

        <section>
          <h4 className="text-sm font-semibold text-text-primary">分類分數</h4>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {DAILY_RADAR_BUCKETS.map((bucket) => (
              <div key={bucket} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                <p className="text-xs font-medium text-text-muted">{BUCKET_LABEL[bucket]}</p>
                <p className="mt-1 font-mono text-lg font-semibold text-text-primary">
                  {candidate.bucket_scores[bucket] === undefined ? "—" : candidate.bucket_scores[bucket]?.toFixed(0)}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h4 className="text-sm font-semibold text-text-primary">命中的觀察規則</h4>
          <div className="mt-3 space-y-3">
            {candidate.matched_rules.length > 0 ? (
              candidate.matched_rules.map((rule) => (
                <article key={rule.rule_id} className="rounded-lg border border-border-subtle bg-surface px-3 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-text-primary">{rule.label}</p>
                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 font-mono text-xs text-badge-neutral-text">
                      規則代碼：{rule.rule_id}
                    </span>
                  </div>
                  <div className="mt-3">
                    <TraceValueList
                      payload={rule.details}
                      emptyText="此規則未附加細節。"
                      formatKey={formatMatchedRuleDetailKey}
                      formatValue={formatMatchedRuleValue}
                    />
                  </div>
                </article>
              ))
            ) : (
              <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
                尚未回傳命中的觀察規則。
              </p>
            )}
          </div>
        </section>

        <section>
          <h4 className="text-sm font-semibold text-text-primary">資料日期</h4>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {dataDateEntries.length > 0 ? (
              dataDateEntries.map(([source, date]) => (
                <div key={source} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                  <p className="text-xs font-medium text-text-muted">{formatDataSourceLabel(source)}</p>
                  <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(date)}</p>
                </div>
              ))
            ) : (
              <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint md:col-span-2">
                尚未回傳候選資料日期。
              </p>
            )}
          </div>
        </section>

        <section>
          <h4 className="text-sm font-semibold text-text-primary">輸入快照摘要</h4>
          <div className="mt-3">
            <TraceValueList payload={candidate.input_snapshot} emptyText="尚未回傳輸入快照。" />
          </div>
        </section>
      </div>
    </details>
  );
}

function DailyRadarCandidateList({
  candidates,
  onSelectCandidate,
  watchlistSymbols,
  watchlistStatusBySymbol,
  onAddWatchlist,
}: {
  candidates: DailyRadarCandidate[];
  onSelectCandidate: (candidate: DailyRadarCandidate) => void;
  watchlistSymbols: Set<string>;
  watchlistStatusBySymbol: Record<string, WatchlistAddStatus>;
  onAddWatchlist: (candidate: DailyRadarCandidate) => void;
}) {
  if (candidates.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center text-sm text-text-faint shadow-sm">
        目前此分類沒有通過濾網的觀察候選。
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-border bg-card shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">候選觀察清單</h2>
          <p className="mt-1 text-xs text-text-muted">依系統內部排序排列；排序不代表勝率或交易建議。</p>
        </div>
        <span className="rounded-full bg-badge-neutral-bg px-3 py-1 text-xs font-medium text-badge-neutral-text">
          共 {candidates.length} 筆
        </span>
      </div>
      <div className="divide-y divide-border-subtle">
        {candidates.map((candidate) => {
          const displayName = getCandidateDisplayName(candidate);
          const normalizedSymbol = normalizeSymbol(candidate.symbol);
          const watchlistStatus = watchlistStatusBySymbol[normalizedSymbol] ?? "idle";
          const isWatchlisted = watchlistSymbols.has(normalizedSymbol);
          const watchlistButtonLabel =
            watchlistStatus === "saving"
              ? "儲存中..."
              : isWatchlisted || watchlistStatus === "success"
                ? "已關注"
                : watchlistStatus === "error"
                  ? "重試加入"
                  : "加入關注";

          return (
            <article
              key={candidate.symbol}
              className="flex flex-col gap-3 px-4 py-4 transition hover:bg-card-hover focus-within:bg-card-hover md:flex-row md:items-stretch md:justify-between"
            >
              <button
                type="button"
                aria-haspopup="dialog"
                onClick={() => onSelectCandidate(candidate)}
                className="min-w-0 flex-1 rounded-lg text-left focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-base font-semibold text-text-primary">{displayName ?? candidate.symbol}</p>
                      {displayName && (
                        <p className="font-mono text-sm font-medium text-text-secondary">{candidate.symbol}</p>
                      )}
                      <span
                        className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${REPEAT_STATUS_CLASS[candidate.repeat_status]}`}
                      >
                        {REPEAT_STATUS_LABEL[candidate.repeat_status]}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs font-medium text-badge-neutral-text">
                        {BUCKET_LABEL[candidate.primary_bucket]}
                      </span>
                      {candidate.risk_labels.length > 0 ? (
                        candidate.risk_labels.map((risk) => (
                          <span
                            key={risk}
                            className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                          >
                            {RISK_LABEL[risk]}
                          </span>
                        ))
                      ) : (
                        <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                          風險標籤待觀察
                        </span>
                      )}
                    </div>
                    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-muted">
                      {getBucketResearchThesis(candidate.primary_bucket)}
                    </p>
                  </div>
                </div>
              </button>
              <div className="flex flex-wrap gap-2 md:w-44 md:flex-col md:self-center">
                <Link
                  to={`/analyze?symbol=${encodeURIComponent(candidate.symbol)}`}
                  className="inline-flex min-h-10 items-center justify-center rounded-lg border border-indigo-500/40 px-3 py-2 text-sm font-medium text-indigo-700 transition hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:text-indigo-300 dark:hover:bg-indigo-950"
                >
                  查看單股完整分析
                </Link>
                <button
                  type="button"
                  onClick={() => onAddWatchlist(candidate)}
                  disabled={watchlistStatus === "saving" || isWatchlisted}
                  className={`inline-flex min-h-10 items-center justify-center rounded-lg border px-3 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-emerald-400 disabled:cursor-not-allowed disabled:opacity-60 ${
                    watchlistStatus === "error"
                      ? "border-red-200 text-red-600 hover:bg-red-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
                      : "border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-800 dark:text-emerald-300 dark:hover:bg-emerald-950"
                  }`}
                  title={isWatchlisted ? "已在關注列表" : "加入關注列表"}
                >
                  {watchlistButtonLabel}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function DailyRadarDetailDrawer({ candidate, onClose }: { candidate: DailyRadarCandidate; onClose: () => void }) {
  const titleId = useId();
  const drawerRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(getActiveHTMLElement());

  useEffect(() => {
    const previouslyFocusedElement = previouslyFocusedElementRef.current;
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab") return;

      const drawer = drawerRef.current;
      if (!drawer) return;

      const focusableElements = getFocusableElements(drawer);
      if (focusableElements.length === 0) {
        event.preventDefault();
        drawer.focus();
        return;
      }

      const firstFocusableElement = focusableElements[0];
      const lastFocusableElement = focusableElements[focusableElements.length - 1];
      const activeElement = getActiveHTMLElement();
      const focusIsOutsideDrawer = !activeElement || !drawer.contains(activeElement);

      if (event.shiftKey) {
        if (focusIsOutsideDrawer || activeElement === firstFocusableElement) {
          event.preventDefault();
          lastFocusableElement.focus();
        }
        return;
      }

      if (focusIsOutsideDrawer || activeElement === lastFocusableElement) {
        event.preventDefault();
        firstFocusableElement.focus();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      if (isFocusableElement(previouslyFocusedElement)) previouslyFocusedElement.focus();
    };
  }, [onClose]);

  const dataDateEntries = Object.entries(candidate.data_dates);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/45 backdrop-blur-sm" onClick={onClose}>
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-border bg-card shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 z-10 border-b border-border-subtle bg-card/95 px-5 py-4 backdrop-blur">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-text-faint">候選追蹤細節</p>
              <h2 id={titleId} className="mt-1 text-xl font-semibold text-text-primary">
                {getCandidateDisplayTitle(candidate)}
              </h2>
              <p className="mt-1 text-sm text-text-muted">追溯本次列入觀察清單的規則與資料輸入。</p>
            </div>
            <button
              type="button"
              ref={closeButtonRef}
              onClick={onClose}
              className="rounded-lg p-2 text-text-faint transition hover:bg-card-hover hover:text-text-secondary focus:outline-none focus:ring-2 focus:ring-indigo-400"
              aria-label="關閉候選追蹤細節"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-5 w-5"
                viewBox="0 0 20 20"
                fill="currentColor"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          </div>
        </div>

        <div className="space-y-5 px-5 py-5">
          <CandidateResearchCard candidate={candidate} />

          <section className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-semibold text-text-primary">觀察狀態</h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {candidate.secondary_buckets.length > 0 ? (
                candidate.secondary_buckets.map((bucket) => (
                  <span
                    key={bucket}
                    className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs font-medium text-badge-neutral-text"
                  >
                    次要分類：{BUCKET_LABEL[bucket]}
                  </span>
                ))
              ) : (
                <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                  無次要分類
                </span>
              )}
              {candidate.risk_labels.length > 0 ? (
                candidate.risk_labels.map((risk) => (
                  <span
                    key={risk}
                    className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                  >
                    {RISK_LABEL[risk]}
                  </span>
                ))
              ) : (
                <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                  未觸發明確風險標籤
                </span>
              )}
            </div>
          </section>

          <BackgroundContextLabels labels={candidate.background_context_labels} />

          <Phase1AvwapContextPanel candidate={candidate} />

          <TechnicalTraceDetails candidate={candidate} dataDateEntries={dataDateEntries} />
        </div>
      </aside>
    </div>
  );
}

function LoadingState() {
  return (
    <section className="rounded-xl border border-border bg-card p-6 text-center shadow-sm">
      <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-border border-t-indigo-600 dark:border-border dark:border-t-indigo-400" />
      <p className="mt-4 text-sm font-medium text-text-primary">資料載入中</p>
      <p className="mt-1 text-xs text-text-muted">正在讀取最新盤後觀察雷達，完成後會顯示掃描日期與資料新鮮度。</p>
    </section>
  );
}

function ErrorState({ error, onRetry }: { error: DailyRadarDisplayError; onRetry: () => void }) {
  return (
    <section className="rounded-xl border border-red-200 bg-red-50 p-6 shadow-sm dark:border-red-900 dark:bg-red-950">
      <p className="text-sm font-semibold text-red-700 dark:text-red-300">每日觀察雷達暫時無法載入</p>
      <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error.message}</p>
      {error.status && <p className="mt-1 text-xs text-red-600 dark:text-red-400">狀態碼：{error.status}</p>}
      <button
        onClick={onRetry}
        className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700"
      >
        重新讀取觀察資料
      </button>
    </section>
  );
}

function StaleRunDataNotice({ runDate, freshnessSummary }: { runDate: string; freshnessSummary: string }) {
  return (
    <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm dark:border-amber-800 dark:bg-amber-950">
      <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">資料日期落後掃描日</p>
      <p className="mt-2 text-sm leading-relaxed text-amber-800 dark:text-amber-300">
        本批次掃描日為 {formatDate(runDate)}，但部分資料日期較晚同步或仍落後；{freshnessSummary}
        結果應作為觀察追蹤與風險脈絡，不代表當前高信心排序。
      </p>
    </section>
  );
}

function WholeRunEmptyState() {
  return (
    <section className="rounded-xl border border-border bg-card p-6 text-center shadow-sm">
      <p className="text-sm font-semibold text-text-primary">今日沒有通過濾網的高品質觀察型態。</p>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-text-muted">
        這代表本次規則掃描沒有產生符合觀察門檻的追蹤候選，並非系統失敗；可持續留意後續資料同步與風險脈絡變化。
      </p>
    </section>
  );
}

function RunSummary({ run }: { run: DailyRadarRunResponse }) {
  const [selectedBucket, setSelectedBucket] = useState<DailyRadarBucket | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<DailyRadarCandidate | null>(null);
  const [watchlistSymbols, setWatchlistSymbols] = useState<Set<string>>(new Set());
  const [watchlistStatusBySymbol, setWatchlistStatusBySymbol] = useState<Record<string, WatchlistAddStatus>>({});
  const dataDateEntries = Object.entries(run.data_dates);
  const freshnessSummary = getFreshnessSummary(run.run_date, run.data_dates);
  const shouldShowStaleNotice = run.status === "stale_data" || hasLaggingRunData(run.run_date, run.data_dates);
  const sortedCandidates = sortDailyRadarCandidates(run.candidates);
  const bucketCounts = getBucketCounts(sortedCandidates);
  const visibleCandidates = selectedBucket
    ? sortedCandidates.filter((candidate) => candidate.primary_bucket === selectedBucket)
    : sortedCandidates;

  useEffect(() => {
    let cancelled = false;

    async function loadWatchlist() {
      try {
        const items = await fetchWatchlistItems();
        if (!cancelled) {
          setWatchlistSymbols(new Set(items.map((item) => normalizeSymbol(item.symbol))));
        }
      } catch {
        if (!cancelled) setWatchlistSymbols(new Set());
      }
    }

    void loadWatchlist();

    return () => {
      cancelled = true;
    };
  }, []);

  async function handleAddWatchlist(candidate: DailyRadarCandidate) {
    const normalizedSymbol = normalizeSymbol(candidate.symbol);
    setWatchlistStatusBySymbol((current) => ({ ...current, [normalizedSymbol]: "saving" }));

    try {
      const item = await createWatchlistItem({ symbol: candidate.symbol });
      const savedSymbol = normalizeSymbol(item.symbol);
      setWatchlistSymbols((current) => new Set(current).add(savedSymbol));
      setWatchlistStatusBySymbol((current) => ({ ...current, [savedSymbol]: "success" }));
    } catch {
      setWatchlistStatusBySymbol((current) => ({ ...current, [normalizedSymbol]: "error" }));
    }
  }

  return (
    <>
      <section className="grid gap-3 md:grid-cols-4">
        <StatCard label="最新掃描日" value={formatDate(run.run_date)} helper="盤後觀察批次" />
        <StatCard label="掃描狀態" value={RUN_STATUS_LABEL[run.status]} helper={RUN_STATUS_HELPER[run.status]} />
        <StatCard label="觀察候選數" value={String(run.candidates.length)} helper="符合規則的追蹤名單" />
        <StatCard
          label="資料新鮮度"
          value={dataDateEntries.length > 0 ? "已回傳" : "待確認"}
          helper={freshnessSummary}
        />
      </section>

      {shouldShowStaleNotice && <StaleRunDataNotice runDate={run.run_date} freshnessSummary={freshnessSummary} />}

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">資料日期與追蹤狀態</h2>
            <p className="mt-1 text-xs text-text-muted">用於確認各資料源是否已同步到本次掃描。</p>
          </div>
          <span className="rounded-full bg-badge-neutral-bg px-3 py-1 text-xs font-medium text-badge-neutral-text">
            {freshnessSummary}
          </span>
        </div>

        <div className="mt-4 grid gap-2 md:grid-cols-3">
          {dataDateEntries.length > 0 ? (
            dataDateEntries.map(([source, date]) => (
              <div key={source} className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
                <p className="text-xs font-medium text-text-muted">{formatDataSourceLabel(source)}</p>
                <p className="mt-1 text-sm font-semibold text-text-primary">{formatDate(date)}</p>
              </div>
            ))
          ) : (
            <p className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint md:col-span-3">
              尚未收到資料日期，請稍後重新讀取觀察資料。
            </p>
          )}
        </div>
      </section>

      {sortedCandidates.length === 0 ? (
        <WholeRunEmptyState />
      ) : (
        <>
          <DailyRadarBucketTabs
            selectedBucket={selectedBucket}
            counts={bucketCounts}
            totalCount={sortedCandidates.length}
            onSelectBucket={setSelectedBucket}
          />

          <DailyRadarCandidateList
            candidates={visibleCandidates}
            watchlistSymbols={watchlistSymbols}
            watchlistStatusBySymbol={watchlistStatusBySymbol}
            onSelectCandidate={setSelectedCandidate}
            onAddWatchlist={(candidate) => void handleAddWatchlist(candidate)}
          />
        </>
      )}

      {selectedCandidate && (
        <DailyRadarDetailDrawer candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} />
      )}
    </>
  );
}

export default function DailyRadarPage() {
  const [run, setRun] = useState<DailyRadarRunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<DailyRadarDisplayError | null>(null);
  const [latestRunUnavailable, setLatestRunUnavailable] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadLatestRun() {
      setLoading(true);
      setError(null);
      setLatestRunUnavailable(false);
      try {
        const data = await fetchLatestDailyRadarRun();
        if (!cancelled) {
          setRun(data);
          setLatestRunUnavailable(false);
        }
      } catch (err) {
        if (!cancelled) {
          if (isNoPublicDailyRadarRunUnavailableError(err)) {
            setRun(null);
            setLatestRunUnavailable(true);
            return;
          }
          setError(toDailyRadarDisplayError(err));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadLatestRun();

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return (
    <div className="space-y-6 px-6 py-4">
      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-faint">盤後觀察雷達</p>
        <div className="mt-2 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-primary md:text-2xl">每日盤後觀察雷達</h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-muted">
              以固定規則追蹤量價、籌碼與資料新鮮度，協助隔日觀察清單整理；內容僅供風險與訊號追蹤。
            </p>
          </div>
          <button
            onClick={() => setReloadKey((key) => key + 1)}
            disabled={loading}
            className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium text-text-muted transition hover:bg-card-hover hover:text-text-secondary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "讀取中…" : "重新整理"}
          </button>
        </div>
      </section>

      {loading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState error={error} onRetry={() => setReloadKey((key) => key + 1)} />
      ) : run ? (
        <RunSummary run={run} />
      ) : latestRunUnavailable ? (
        <WholeRunEmptyState />
      ) : null}
    </div>
  );
}
