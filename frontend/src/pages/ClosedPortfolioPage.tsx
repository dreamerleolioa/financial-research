import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { setAsyncMapValue } from "../lib/asyncMap";
import {
  createPositionLifecycleReview,
  fetchClosedPortfolioItems,
  fetchOrCreateTradeReview,
  fetchPositionGroupEvents,
} from "../lib/closedPortfolioApi";
import { formatPrice } from "../lib/formatters";
import type {
  ClosedPortfolioItem,
  LifecycleTextItem,
  PositionEvent,
  PositionGroupEventsResponse,
  PositionLifecycleDataQuality,
  PositionLifecycleEntrySequence,
  PositionLifecycleEventFact,
  PositionLifecycleEventIndicatorSnapshot,
  PositionLifecycleExitSequence,
  PositionLifecycleMetrics,
  PositionLifecycleReviewResponse,
  TradeReviewDataQuality,
  TradeReviewHoldingSection,
  TradeReviewResponse,
  TradeReviewResultMetrics,
  TradeReviewSection,
  TradeReviewUserReadableConclusion,
} from "../lib/portfolioTypes";
import {
  ADD_ENTRY_CONDITION_LABEL,
  DEFAULT_STOP_RULE_LABEL,
  ENTRY_RECORD_REASON_LABEL,
  PLANNED_HOLDING_PERIOD_LABEL,
} from "../lib/portfolioLabels";

type PeriodKey = "1d" | "1w" | "1m" | "1q" | "1y";
type CopyStatus = "idle" | "success" | "error";

interface PeriodOption {
  key: PeriodKey;
  label: string;
  days: number;
}

interface ClosedPortfolioGroup {
  position_group_id: string;
  symbol: string;
  entry_date: string;
  entry_price: number;
  totalClosedQuantity: number;
  totalRealizedPnl: number;
  exitBatchCount: number;
  items: ClosedPortfolioItem[];
}

interface ReviewModalProps {
  item: ClosedPortfolioItem;
  review: TradeReviewResponse | null;
  loading: boolean;
  error: string | null;
  copyStatus: CopyStatus;
  onCopyEvidence: () => void;
  onClose: () => void;
}

interface TimelineModalProps {
  group: ClosedPortfolioGroup;
  timeline: PositionGroupEventsResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

interface LifecycleReviewModalProps {
  group: ClosedPortfolioGroup;
  review: PositionLifecycleReviewResponse | null;
  loading: boolean;
  error: string | null;
  copyStatus: CopyStatus;
  onCopyEvidence: () => void;
  onClose: () => void;
}

interface LifecyclePlanRelationGroup {
  label: string;
  items: LifecycleTextItem[];
}

const PERIOD_OPTIONS: PeriodOption[] = [
  { key: "1d", label: "1天", days: 1 },
  { key: "1w", label: "1週", days: 7 },
  { key: "1m", label: "1月", days: 30 },
  { key: "1q", label: "1季", days: 90 },
  { key: "1y", label: "1年", days: 365 },
];

const SELECTED_PERIOD_STORAGE_KEY = "closedPortfolio.selectedPeriod";

const CLASSIFICATION_LABEL: Record<string, string> = {
  strong_entry: "進場品質佳",
  good_entry: "進場品質佳",
  acceptable_entry: "進場尚可",
  neutral_entry: "進場中性",
  weak_entry: "進場偏弱",
  late_entry: "進場偏晚",
  early_entry: "進場偏早",
  chasing_entry: "追高進場",
  chase_entry: "追高進場",
  breakout_entry: "突破進場",
  pullback_entry: "回測進場",
  range_entry: "區間進場",
  insufficient_data: "資料不足",
  strong_hold: "持有紀律佳",
  good_hold: "持有紀律佳",
  acceptable_hold: "持有尚可",
  neutral_hold: "持有中性",
  weak_hold: "持有偏弱",
  overheld: "持有過久",
  premature_exit: "風險處理偏早",
  good_exit: "結案品質佳",
  planned_exit: "依計畫結案",
  profit_taking_exit: "獲利保護結案",
  profit_protection_exit: "獲利保護結案",
  stop_loss_exit: "風險控制結案",
  late_stop_exit: "風險控制偏晚",
  early_profit_exit: "獲利保護偏早",
  technical_break_exit: "技術破位後結案",
  panic_exit: "情緒性風險處理",
  weak_exit: "結案品質偏弱",
  rule_based_trade_review: "規則化檢討",
  disciplined_operation: "執行紀律佳",
  rule_followed: "規則執行到位",
  rule_violation: "規則執行偏離",
  needs_improvement: "需改善執行紀律",
};

const LIFECYCLE_CLASSIFICATION_LABEL: Record<string, string> = {
  insufficient_data: "決策脈絡不足",
  averaging_down_into_weakness: "弱勢中新增批次",
  disciplined_scale_out: "分批降低曝險保護獲利",
  risk_reduction_exit: "破位後降低風險",
  premature_scale_out: "可能過早降低曝險",
  late_scale_out: "風險處理偏晚",
  coherent_position_management: "部位管理一致",
  add_entry_plan_violation: "新增批次計畫偏離",
  ma20_pullback_supported: "回測 20 日線獲得支持",
  unacted_stop_rule_break: "風險控制規則觸發後未行動",
  holding_period_needs_review: "持有期間需檢討",
};

const LIFECYCLE_TIER_LABEL: Record<string, string> = {
  needs_review: "需檢討",
  insufficient_context: "脈絡不足",
  constructive: "具建設性",
  mixed: "混合結論",
};

const LIFECYCLE_TIER_CLASS: Record<string, string> = {
  needs_review: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  insufficient_context: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  constructive: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  mixed: "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
};

const LIFECYCLE_PROVENANCE_CLASS: Record<string, string> = {
  "real events": "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  "synthetic events": "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
  "mixed provenance": "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
};

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
  strong: "高",
  moderate: "中",
  weak: "低",
};

const MARKET_REGIME_LABEL: Record<string, string> = {
  bullish: "多頭環境",
  bearish: "空頭環境",
  sideways: "盤整環境",
  neutral: "中性環境",
  uptrend: "上升趨勢",
  downtrend: "下降趨勢",
  range_bound: "區間震盪",
  consolidation: "整理盤",
  high_volatility: "高波動環境",
  strong_momentum: "強動能環境",
  insufficient_data: "資料不足",
  medium_volatility: "中波動環境",
  low_volatility: "低波動環境",
  risk_on: "風險偏好升溫",
  risk_off: "風險偏好降溫",
};

const DETECTED_EVENT_TYPE_LABEL: Record<string, string> = {
  ma5_break: "5 日均線破位",
  ma20_break: "20 日均線破位",
  ma60_break: "60 日均線破位",
  new_high_continuation: "持有期間創高",
  volume_down_day: "放量下跌",
  support_break: "跌破近期支撐",
  rsi_overheated: "RSI 過熱",
  ma5_reclaim: "站回 5 日均線",
  ma20_reclaim: "站回 20 日均線",
  ma60_reclaim: "站回 60 日均線",
  stop_loss_hit: "觸及風險控制",
  trailing_stop_hit: "觸及動態風險控制",
  profit_target_hit: "達到獲利保護目標",
  profit_giveback: "獲利回吐",
  drawdown_alert: "回撤警示",
  high_volatility: "高波動事件",
  volume_spike: "量能放大",
  gap_down: "跳空下跌",
  gap_up: "跳空上漲",
};

const DATA_QUALITY_STATUS_LABEL: Record<string, string> = {
  ok: "資料可用",
  sufficient: "資料充足",
  partial: "資料部分不足",
  insufficient: "資料不足",
  unavailable: "資料不可用",
  complete: "資料完整",
};

const INSUFFICIENT_DATA_LABEL: Record<string, string> = {
  entry_indicators: "進場技術指標",
  exit_indicators: "出場技術指標",
  path_metrics: "持有期間路徑指標",
  detected_events: "持有期間偵測事件",
  source_data: "原始行情資料",
  ohlcv: "價格與成交量資料",
  price_data: "價格資料",
  volume_data: "成交量資料",
  technical_indicators: "技術指標資料",
  institutional_flow: "法人買賣超資料",
  market_context: "大盤環境資料",
  entry_date: "進場日期資料",
  exit_date: "出場日期資料",
  holding_path_prices: "持有期間價格資料",
  entry_price: "進場價格資料",
  entry_ma20: "進場 MA20",
  entry_ma60: "進場 MA60",
  entry_rsi14: "進場 RSI14",
  entry_volume_ratio: "進場量比",
  exit_ma20: "出場 MA20",
  exit_ma60: "出場 MA60",
  exit_rsi14: "出場 RSI14",
  exit_volume_ratio: "出場量比",
};

const POSITION_EVENT_TYPE_LABEL: Record<PositionEvent["event_type"], string> = {
  initial_entry: "初始進場",
  add_entry: "新增進場批次",
  partial_exit: "部分結案",
  full_exit: "完整結案",
  manual_adjustment: "手動調整",
};

const POSITION_EVENT_TYPE_CLASS: Record<PositionEvent["event_type"], string> = {
  initial_entry: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  add_entry: "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
  partial_exit: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  full_exit: "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
  manual_adjustment: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300",
};

const POSITION_EVENT_SOURCE_LABEL: Record<PositionEvent["source"], string> = {
  synthetic_from_portfolio_row: "由既有持股列回填",
  user_backfilled: "使用者事後補登",
  user_recorded_at_event_time: "使用者於事件當下紀錄",
  manual_record_correction: "手動紀錄修正",
  not_recorded: "未記錄來源",
};

const POSITION_EVENT_SOURCE_HELPER: Record<PositionEvent["source"], string> = {
  synthetic_from_portfolio_row: "這是根據既有 portfolio row 合成的事件，僅代表交易事實，不代表當時決策意圖。",
  user_backfilled: "這是事後補登資料，決策脈絡可能不完整。",
  user_recorded_at_event_time: "這是事件發生時建立的紀錄。",
  manual_record_correction: "這是為修正紀錄而建立的事件，請以校正用途解讀。",
  not_recorded: "來源未記錄，不能推論當時意圖。",
};

const TIMELINE_REASON_CATEGORY_LABEL: Record<string, string> = {
  technical: "技術面",
  institutional_flow: "籌碼面",
  fundamental: "基本面",
  news: "消息面",
  risk_control: "風控",
  plan_execution: "計畫執行",
  emotional: "情緒",
  record_correction: "紀錄修正",
  not_recorded: "未記錄",
};

const TIMELINE_REASON_CODE_LABEL: Record<string, string> = {
  breakout_confirmation: "突破確認",
  pullback_held_support: "回測守住支撐",
  pullback_held_ma20: "回測守住 20 日線",
  institutional_flow_strengthened: "法人籌碼轉強",
  fundamental_thesis_improved: "基本面假設改善",
  event_or_news_catalyst: "事件／消息催化",
  long_term_accumulation: "長期分批佈局",
  value_revaluation: "價值重估",
  other: "其他固定理由",
  planned_scale_in: "依計畫新增批次",
  averaging_down: "攤平新增批次",
  chasing_momentum: "追逐動能",
  target_reached: "達到目標",
  trailing_stop_hit: "觸及移動停利",
  support_broken: "支撐跌破",
  ma20_lost: "跌破 20 日線",
  institutional_flow_weakened: "法人籌碼轉弱",
  fundamental_thesis_broken: "基本面假設破壞",
  news_risk_increased: "消息風險升高",
  risk_reduction: "降低風險",
  profit_protection: "保護獲利",
  planned_scale_out: "依計畫分批降低曝險",
  stop_loss: "風險控制",
  emotional_exit: "情緒性風險處理",
  manual_record_correction: "手動紀錄修正",
  not_recorded: "未記錄",
};

const TIMELINE_PLAN_ADHERENCE_LABEL: Record<string, string> = {
  yes: "符合計畫",
  partial: "部分符合計畫",
  no: "未符合計畫",
  not_recorded: "未記錄",
};

const TIMELINE_CONFIDENCE_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
  not_recorded: "未記錄",
};

function isValidPeriodKey(value: string | null): value is PeriodKey {
  return PERIOD_OPTIONS.some((option) => option.key === value);
}

function readStoredPeriod(): PeriodKey {
  try {
    const storedPeriod = window.localStorage.getItem(SELECTED_PERIOD_STORAGE_KEY);
    return isValidPeriodKey(storedPeriod) ? storedPeriod : "1m";
  } catch {
    return "1m";
  }
}

function writeStoredPeriod(period: PeriodKey): boolean {
  try {
    window.localStorage.setItem(SELECTED_PERIOD_STORAGE_KEY, period);
    return true;
  } catch {
    return false;
  }
}

function toLocalDate(value: string): Date | null {
  const [yearText, monthText, dayText] = value.slice(0, 10).split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null;
  return new Date(year, month - 1, day);
}

function getToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function isWithinPeriod(exitDate: string, days: number, today: Date): boolean {
  const date = toLocalDate(exitDate);
  if (!date) return false;
  const start = new Date(today);
  start.setDate(today.getDate() - days + 1);
  return date >= start && date <= today;
}

function groupClosedItems(items: ClosedPortfolioItem[]): ClosedPortfolioGroup[] {
  const groups: ClosedPortfolioGroup[] = [];
  const groupMap = new Map<string, ClosedPortfolioGroup>();

  for (const item of items) {
    let group = groupMap.get(item.position_group_id);
    if (!group) {
      group = {
        position_group_id: item.position_group_id,
        symbol: item.symbol,
        entry_date: item.entry_date,
        entry_price: item.entry_price,
        totalClosedQuantity: 0,
        totalRealizedPnl: 0,
        exitBatchCount: 0,
        items: [],
      };
      groupMap.set(item.position_group_id, group);
      groups.push(group);
    }

    group.totalClosedQuantity += item.exit_quantity;
    group.totalRealizedPnl += item.realized_pnl;
    group.exitBatchCount += 1;
    group.items.push(item);
  }

  return groups;
}

function getSignedPriceText(value: number | null | undefined, symbol?: string): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${formatPrice(value, symbol)}`;
}

function getSignedPercentText(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatPlainValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isFinite(value) ? new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 4 }).format(value) : "—";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  return JSON.stringify(value, null, 2);
}

function formatClassificationLabel(value: string): string {
  return CLASSIFICATION_LABEL[value] ?? "其他檢討分類";
}

function formatConfidenceLabel(value: string): string {
  return CONFIDENCE_LABEL[value] ?? "其他";
}

function formatMarketRegimeLabel(value: string): string {
  return MARKET_REGIME_LABEL[value] ?? "其他市場環境";
}

function formatDetectedEventTypeLabel(value: string): string {
  return DETECTED_EVENT_TYPE_LABEL[value] ?? "其他偵測事件";
}

function formatDataQualityStatusLabel(value: string): string {
  return DATA_QUALITY_STATUS_LABEL[value] ?? "其他資料狀態";
}

function formatInsufficientDataLabel(value: string): string {
  return INSUFFICIENT_DATA_LABEL[value] ?? "其他資料項目";
}

function formatLifecycleClassificationLabel(value: string | null | undefined): string {
  if (!value) return "尚無分類";
  return LIFECYCLE_CLASSIFICATION_LABEL[value] ?? `其他生命週期分類（${value}）`;
}

function formatEntryRecordReason(value: string | null | undefined): string {
  if (!value) return "未記錄";
  return (ENTRY_RECORD_REASON_LABEL as Record<string, string>)[value] ?? formatTimelineReasonCode(value);
}

function formatPlannedHoldingPeriod(value: string | null | undefined): string {
  if (!value) return "未記錄";
  return (PLANNED_HOLDING_PERIOD_LABEL as Record<string, string>)[value] ?? `其他持有期間（${value}）`;
}

function formatDefaultStopRule(value: string | null | undefined): string {
  if (!value) return "未記錄";
  return (DEFAULT_STOP_RULE_LABEL as Record<string, string>)[value] ?? `其他風險控制規則（${value}）`;
}

function formatAddEntryCondition(value: string | null | undefined): string {
  if (!value) return "未記錄";
  return (ADD_ENTRY_CONDITION_LABEL as Record<string, string>)[value] ?? `其他新增批次條件（${value}）`;
}

function formatLifecycleTierLabel(value: string | null | undefined): string {
  if (!value) return "尚無層級";
  return LIFECYCLE_TIER_LABEL[value] ?? `其他層級（${value}）`;
}

function formatPositionEventTypeLabel(value: string | null | undefined): string {
  if (!value) return "其他事件";
  return (POSITION_EVENT_TYPE_LABEL as Record<string, string>)[value] ?? `其他事件（${value}）`;
}

function formatPositionEventSourceLabel(value: string | null | undefined): string {
  if (!value) return "未記錄來源";
  return (POSITION_EVENT_SOURCE_LABEL as Record<string, string>)[value] ?? `其他來源（${value}）`;
}

function formatPositionEventSourceHelper(value: string | null | undefined): string {
  if (!value) return "來源未記錄，不能推論當時意圖。";
  return (POSITION_EVENT_SOURCE_HELPER as Record<string, string>)[value] ?? "這是已保存事件來源，請依其來源欄位解讀。";
}

function getPositionEventTypeClass(value: string | null | undefined): string {
  return value ? (POSITION_EVENT_TYPE_CLASS as Record<string, string>)[value] ?? POSITION_EVENT_TYPE_CLASS.manual_adjustment : POSITION_EVENT_TYPE_CLASS.manual_adjustment;
}

function getLifecycleTierClass(value: string | null | undefined): string {
  return value ? LIFECYCLE_TIER_CLASS[value] ?? "border-border-subtle bg-badge-neutral-bg text-badge-neutral-text" : "border-border-subtle bg-badge-neutral-bg text-badge-neutral-text";
}

function getLifecycleProvenance(events: PositionLifecycleEventFact[]): "real events" | "synthetic events" | "mixed provenance" {
  if (events.length === 0) return "mixed provenance";
  const sources = events.map((event) => event.source);
  const hasSynthetic = sources.some((source) => source === "synthetic_from_portfolio_row");
  const hasUncertain = sources.some((source) => !source || source === "not_recorded");
  const hasReal = sources.some((source) => source && source !== "synthetic_from_portfolio_row" && source !== "not_recorded");
  if (hasSynthetic && !hasReal && !hasUncertain) return "synthetic events";
  if (hasReal && !hasSynthetic && !hasUncertain) return "real events";
  return "mixed provenance";
}

function hasLifecycleDecisionContextWarning(review: PositionLifecycleReviewResponse | null): boolean {
  const result = review?.review_result;
  const labels = result?.lifecycle_review?.classification?.labels ?? [];
  return result?.decision_context?.status !== "present" || labels.includes("insufficient_data");
}

function hasBackfilledLifecyclePlanCaveat(review: PositionLifecycleReviewResponse | null): boolean {
  const decisionContext = review?.review_result.decision_context;
  return decisionContext?.source === "user_backfilled" || decisionContext?.created_after_entry === true;
}

function getLifecycleEvidenceForEvent(items: LifecycleTextItem[] | undefined, event: PositionLifecycleEventFact): LifecycleTextItem[] {
  if (!items || !event.event_key) return [];
  const factRef = `event_facts.${event.event_key}`;
  const snapshotRef = `event_indicator_snapshots.${event.event_key}`;
  return items.filter((item) => item.source_refs.includes(factRef) || item.source_refs.includes(snapshotRef));
}

function getInitialEntryEventFact(events: PositionLifecycleEventFact[]): PositionLifecycleEventFact | undefined {
  return events.find((event) => event.event_type === "initial_entry");
}

function hasLifecyclePlanRelationSourceRefs(item: LifecycleTextItem): boolean {
  return item.source_refs.some(
    (sourceRef) => sourceRef.startsWith("event_facts.")
      || sourceRef.startsWith("event_indicator_snapshots.")
      || sourceRef.startsWith("decision_context"),
  );
}

function getLifecyclePlanRelationGroups(review: PositionLifecycleReviewResponse): LifecyclePlanRelationGroup[] {
  const lifecycleReview = review.review_result.lifecycle_review;
  const classification = lifecycleReview?.classification;
  const groups: LifecyclePlanRelationGroup[] = [
    { label: "分類依據", items: classification?.reasons ?? [] },
    { label: "判讀限制", items: classification?.caveats ?? [] },
    { label: "做得好的地方", items: lifecycleReview?.what_worked ?? [] },
    { label: "需要檢討的地方", items: lifecycleReview?.what_needs_review ?? [] },
  ];

  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter(hasLifecyclePlanRelationSourceRefs),
    }))
    .filter((group) => group.items.length > 0);
}

function formatSourceRefs(sourceRefs: string[] | undefined): string {
  return sourceRefs && sourceRefs.length > 0 ? sourceRefs.join("、") : "未標記來源";
}

function getStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function hasDataQualityPrompt(dataQuality: TradeReviewDataQuality | undefined): boolean {
  if (!dataQuality) return false;
  const notes = getStringArray(dataQuality.notes);
  const insufficientData = getStringArray(dataQuality.insufficient_data);
  return dataQuality.status === "insufficient" || notes.length > 0 || insufficientData.length > 0;
}

function formatTimelineReasonCategory(value: string | null): string {
  if (!value) return "未記錄";
  return TIMELINE_REASON_CATEGORY_LABEL[value] ?? `其他原因分類（${value}）`;
}

function formatTimelineReasonCode(value: string | null): string {
  if (!value) return "未記錄";
  return TIMELINE_REASON_CODE_LABEL[value] ?? `其他原因代碼（${value}）`;
}

function formatTimelinePlanAdherence(value: string | null): string {
  if (!value) return "未記錄";
  return TIMELINE_PLAN_ADHERENCE_LABEL[value] ?? `其他計畫狀態（${value}）`;
}

function formatTimelineConfidence(value: string | null): string {
  if (!value) return "未記錄";
  return TIMELINE_CONFIDENCE_LABEL[value] ?? `其他信心水準（${value}）`;
}

function isNotRecorded(value: string | null): boolean {
  return value == null || value === "not_recorded";
}

function hasInsufficientDecisionContext(event: PositionEvent): boolean {
  return isNotRecorded(event.plan_adherence) || isNotRecorded(event.reason_code) || isNotRecorded(event.confidence_level);
}

function TimelineMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
      <p className="text-xs text-text-faint">{label}</p>
      <p className="mt-1 font-mono text-sm font-medium text-text-primary">{value}</p>
    </div>
  );
}

function ReviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
      <p className="text-xs text-text-faint">{label}</p>
      <p className="mt-1 font-mono text-sm font-medium text-text-primary">{value}</p>
    </div>
  );
}

function TradeResultSection({ metrics, symbol }: { metrics: TradeReviewResultMetrics | undefined; symbol: string }) {
  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-text-primary">交易結果</h3>
        <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">單筆結案批次</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <ReviewMetric label="進場日期" value={formatPlainValue(metrics?.entry_date)} />
        <ReviewMetric label="結案日期" value={formatPlainValue(metrics?.exit_date)} />
        <ReviewMetric label="進場價格" value={formatPrice(metrics?.entry_price, symbol)} />
        <ReviewMetric label="結案價格" value={formatPrice(metrics?.exit_price, symbol)} />
        <ReviewMetric label="持有天數" value={metrics?.holding_days == null ? "—" : `${metrics.holding_days} 天`} />
        <ReviewMetric label="已實現損益" value={getSignedPriceText(metrics?.realized_pnl, symbol)} />
        <ReviewMetric label="已實現報酬" value={getSignedPercentText(metrics?.realized_return_pct)} />
        <ReviewMetric label="最高浮盈" value={getSignedPercentText(metrics?.max_profit_pct)} />
        <ReviewMetric label="最大回撤" value={getSignedPercentText(metrics?.max_drawdown_pct)} />
        <ReviewMetric label="獲利回吐" value={getSignedPercentText(metrics?.profit_giveback_pct)} />
      </div>
    </article>
  );
}

function UserReadableConclusionCard({ conclusion }: { conclusion: TradeReviewUserReadableConclusion | undefined }) {
  if (!conclusion) return null;

  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-primary">紀律檢討結論</h3>
        {conclusion.overall_verdict && (
          <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
            {conclusion.overall_verdict}
          </span>
        )}
      </div>

      <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
        <p className="text-xs font-medium text-text-muted">整體結論</p>
        <p className="mt-1 text-base font-semibold text-text-primary">
          {conclusion.overall_verdict_label || "尚無整體結論。"}
        </p>
      </div>

      <div className="mt-3 rounded-lg border border-border-subtle bg-surface px-3 py-2">
        <p className="text-xs font-medium text-text-muted">一句話原因</p>
        <p className="mt-1 text-sm leading-relaxed text-text-secondary">
          {conclusion.one_sentence_reason || "尚無一句話原因。"}
        </p>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <ReviewSignalList label="判斷依據" values={conclusion.evidence} />
        <ReviewSignalList label="下次規則" values={conclusion.next_time_rules} />
      </div>
    </article>
  );
}

function ReviewSignalList({ label, values }: { label: string; values: string[] | undefined }) {
  if (!values || values.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium text-text-muted">{label}</p>
      <ul className="mt-1 space-y-1">
        {values.map((value, index) => (
          <li key={`${label}-${index}`} className="rounded-md bg-badge-neutral-bg px-2 py-1 text-xs leading-relaxed text-badge-neutral-text">
            {value}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DetectedEventsList({ events }: { events: Record<string, unknown>[] | undefined }) {
  if (!events || events.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium text-text-muted">偵測事件</p>
      <div className="mt-1 space-y-1.5">
        {events.map((event, index) => {
          const date = typeof event.date === "string" ? event.date : null;
          const type = typeof event.type === "string" ? event.type : "event";
          const description = typeof event.summary === "string"
            ? event.summary
            : typeof event.description === "string" ? event.description : null;
          return (
            <div key={`${type}-${date ?? index}`} className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-xs text-text-secondary">
              <div className="flex flex-wrap items-center gap-2">
                {date && <span className="font-mono text-text-faint">{date}</span>}
                <span className="font-medium text-text-primary">{formatDetectedEventTypeLabel(type)}</span>
              </div>
              {description && <p className="mt-1 leading-relaxed text-text-muted">{description}</p>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReviewSectionCard({ title, section }: { title: string; section: TradeReviewSection | TradeReviewHoldingSection | undefined }) {
  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
        <div className="flex flex-wrap gap-1.5">
          {section?.classification && (
            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
              {formatClassificationLabel(section.classification)}
            </span>
          )}
          {section?.confidence && (
            <span className="rounded-md bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              信心 {formatConfidenceLabel(section.confidence)}
            </span>
          )}
          {section?.market_regime && (
            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
              {formatMarketRegimeLabel(section.market_regime)}
            </span>
          )}
        </div>
      </div>
      {section?.summary ? (
        <p className="text-sm leading-relaxed text-text-secondary">{section.summary}</p>
      ) : (
        <p className="text-sm text-text-faint">尚無摘要。</p>
      )}
      <div className="mt-3 space-y-3">
        <ReviewSignalList label="支持訊號" values={section?.supporting_signals} />
        <ReviewSignalList label="衝突訊號" values={section?.conflicting_signals} />
        <ReviewSignalList label="注意事項" values={section?.caveats} />
        <DetectedEventsList events={(section as TradeReviewHoldingSection | undefined)?.detected_events} />
      </div>
    </article>
  );
}

function DataQualitySection({ dataQuality }: { dataQuality: TradeReviewDataQuality }) {
  const notes = getStringArray(dataQuality.notes);
  const insufficientData = getStringArray(dataQuality.insufficient_data).map(formatInsufficientDataLabel);
  return (
    <article className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm dark:border-amber-800 dark:bg-amber-950">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-amber-800 dark:text-amber-300">資料品質提示</h3>
        {dataQuality.status && <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900 dark:text-amber-200">{formatDataQualityStatusLabel(dataQuality.status)}</span>}
      </div>
      {notes.length > 0 && <ReviewSignalList label="提示" values={notes} />}
      {insufficientData.length > 0 && <ReviewSignalList label="資料不足欄位" values={insufficientData} />}
    </article>
  );
}

function LifecycleSourceRefs({ sourceRefs }: { sourceRefs: string[] | undefined }) {
  return (
    <p className="mt-1 text-xs text-text-faint">
      來源：{formatSourceRefs(sourceRefs)}
    </p>
  );
}

function LifecycleTextItemBlock({ item }: { item: LifecycleTextItem }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
      <p className="text-sm leading-relaxed text-text-secondary">{item.text}</p>
      <LifecycleSourceRefs sourceRefs={item.source_refs} />
    </div>
  );
}

function LifecycleTextItemList({ label, items, emptyText }: { label: string; items: LifecycleTextItem[] | undefined; emptyText?: string }) {
  const values = items ?? [];
  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-text-primary">{label}</h3>
      {values.length > 0 ? (
        <div className="mt-3 space-y-2">
          {values.map((item, index) => <LifecycleTextItemBlock key={`${label}-${index}`} item={item} />)}
        </div>
      ) : (
        <p className="mt-2 text-sm text-text-faint">{emptyText ?? "尚無資料。"}</p>
      )}
    </article>
  );
}

function LifecyclePlanFactCard({ label, value, sourceRefs }: { label: string; value: string; sourceRefs: string[] | undefined }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2">
      <p className="text-xs text-text-faint">{label}</p>
      <p className="mt-1 text-sm font-medium text-text-primary">{value}</p>
      <LifecycleSourceRefs sourceRefs={sourceRefs} />
    </div>
  );
}

function LifecyclePlanReviewRelationSection({ review }: { review: PositionLifecycleReviewResponse }) {
  const result = review.review_result;
  const eventFacts = result.event_facts ?? [];
  const initialEntry = getInitialEntryEventFact(eventFacts);
  const decisionContext = result.decision_context;
  const lifecycleLabels = result.lifecycle_review?.classification?.labels ?? [];
  const relationGroups = getLifecyclePlanRelationGroups(review);
  const decisionContextSourceRefs = decisionContext
    ? {
        plannedHoldingPeriod: ["decision_context.planned_holding_period"],
        defaultStopRule: ["decision_context.default_stop_rule"],
        addEntryCondition: ["decision_context.add_entry_condition"],
      }
    : {
        plannedHoldingPeriod: undefined,
        defaultStopRule: undefined,
        addEntryCondition: undefined,
      };

  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">原始計畫與檢討關聯</h3>
          <p className="mt-1 text-xs leading-relaxed text-text-muted">
            只呈現已保存的初始進場事件與 decision_context 固定欄位，並列出引用事件、指標快照或 plan 欄位的 lifecycle 結論片段。
          </p>
        </div>
        <span className="w-fit rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">lifecycle-only</span>
      </div>

      <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-4">
        <LifecyclePlanFactCard
          label="初始進場理由"
          value={formatEntryRecordReason(initialEntry?.reason_code)}
          sourceRefs={initialEntry?.event_key ? [`event_facts.${initialEntry.event_key}`] : undefined}
        />
        <LifecyclePlanFactCard
          label="預計持有期間"
          value={formatPlannedHoldingPeriod(decisionContext?.planned_holding_period)}
          sourceRefs={decisionContextSourceRefs.plannedHoldingPeriod}
        />
        <LifecyclePlanFactCard
          label="預設風險控制規則"
          value={formatDefaultStopRule(decisionContext?.default_stop_rule)}
          sourceRefs={decisionContextSourceRefs.defaultStopRule}
        />
        <LifecyclePlanFactCard
          label="新增批次條件"
          value={formatAddEntryCondition(decisionContext?.add_entry_condition)}
          sourceRefs={decisionContextSourceRefs.addEntryCondition}
        />
      </div>

      {lifecycleLabels.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-text-muted">Lifecycle labels</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {lifecycleLabels.map((label) => (
              <span key={label} className="rounded-md bg-badge-neutral-bg px-2 py-1 text-xs text-badge-neutral-text">
                {formatLifecycleClassificationLabel(label)}
              </span>
            ))}
          </div>
        </div>
      )}

      {relationGroups.length > 0 ? (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {relationGroups.map((group) => (
            <div key={group.label} className="space-y-2">
              <p className="text-xs font-medium text-text-muted">{group.label}</p>
              {group.items.map((item, index) => <LifecycleTextItemBlock key={`${group.label}-${index}`} item={item} />)}
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-text-faint">
          尚無引用 event_facts、event_indicator_snapshots 或 decision_context 的關聯片段。
        </p>
      )}
    </article>
  );
}

function LifecycleDataQualitySection({ dataQuality, notes }: { dataQuality: PositionLifecycleDataQuality | undefined; notes: LifecycleTextItem[] | undefined }) {
  const noteTexts = getStringArray(dataQuality?.notes);
  const insufficientData = getStringArray(dataQuality?.insufficient_data).map(formatInsufficientDataLabel);
  const lifecycleNotes = notes ?? [];
  if (!dataQuality && lifecycleNotes.length === 0) return null;

  return (
    <article className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm dark:border-amber-800 dark:bg-amber-950">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-amber-800 dark:text-amber-300">資料品質與決策脈絡</h3>
        {dataQuality?.status && (
          <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900 dark:text-amber-200">
            {formatDataQualityStatusLabel(dataQuality.status)}
          </span>
        )}
      </div>
      <div className="space-y-3">
        {lifecycleNotes.map((item, index) => <LifecycleTextItemBlock key={`data-quality-${index}`} item={item} />)}
        {noteTexts.length > 0 && <ReviewSignalList label="資料品質提示" values={noteTexts} />}
        {insufficientData.length > 0 && <ReviewSignalList label="資料不足欄位" values={insufficientData} />}
      </div>
    </article>
  );
}

function LifecycleOverallSection({ review }: { review: PositionLifecycleReviewResponse }) {
  const lifecycleReview = review.review_result.lifecycle_review;
  const classification = lifecycleReview?.classification;
  const tier = classification?.tier;
  const labels = classification?.labels ?? [];

  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">整體結果</h3>
          <p className="mt-1 text-xs text-text-muted">這是整個 position group 的多次進出生命週期檢討，不是單筆結案批次檢討。</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {tier && <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${getLifecycleTierClass(tier)}`}>{formatLifecycleTierLabel(tier)}</span>}
          {classification?.primary_label && (
            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
              {formatLifecycleClassificationLabel(classification.primary_label)}
            </span>
          )}
        </div>
      </div>

      {lifecycleReview?.overall_conclusion ? (
        <LifecycleTextItemBlock item={lifecycleReview.overall_conclusion} />
      ) : (
        <p className="text-sm text-text-faint">尚無整體結論。</p>
      )}

      {labels.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {labels.map((label) => (
            <span key={label} className="rounded-md bg-badge-neutral-bg px-2 py-1 text-xs text-badge-neutral-text">
              {formatLifecycleClassificationLabel(label)}
            </span>
          ))}
        </div>
      )}

      {(classification?.reasons?.length || classification?.caveats?.length) && (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <LifecycleTextItemList label="分類依據" items={classification?.reasons} />
          <LifecycleTextItemList label="判讀限制" items={classification?.caveats} />
        </div>
      )}
    </article>
  );
}

function LifecyclePerspectiveCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
      <div className="mt-3 grid grid-cols-2 gap-2">{children}</div>
    </article>
  );
}

function LifecyclePerspectives({ metrics, entrySequence, exitSequence }: { metrics: PositionLifecycleMetrics | undefined; entrySequence: PositionLifecycleEntrySequence | undefined; exitSequence: PositionLifecycleExitSequence | undefined }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <LifecyclePerspectiveCard title="進場視角">
        <ReviewMetric label="進場次數" value={formatPlainValue(entrySequence?.entry_count)} />
        <ReviewMetric label="新增批次次數" value={formatPlainValue(entrySequence?.add_entry_count)} />
        <ReviewMetric label="攤平新增批次" value={formatPlainValue(entrySequence?.average_down_count)} />
        <ReviewMetric label="首筆相對 MA20" value={getSignedPercentText(entrySequence?.initial_entry_vs_ma20_pct)} />
      </LifecyclePerspectiveCard>
      <LifecyclePerspectiveCard title="持有視角">
        <ReviewMetric label="持有天數" value={metrics?.total_holding_days_from_first_entry == null ? "—" : `${metrics.total_holding_days_from_first_entry} 天`} />
        <ReviewMetric label="曝險天數" value={metrics?.active_exposure_days == null ? "—" : `${metrics.active_exposure_days} 天`} />
        <ReviewMetric label="最高浮盈" value={getSignedPercentText(metrics?.max_unrealized_profit_pct)} />
        <ReviewMetric label="最大回撤" value={getSignedPercentText(metrics?.max_unrealized_drawdown_pct)} />
      </LifecyclePerspectiveCard>
      <LifecyclePerspectiveCard title="部位管理視角">
        <ReviewMetric label="加權成本" value={formatPrice(metrics?.weighted_average_entry_price)} />
        <ReviewMetric label="最大部位" value={formatPlainValue(metrics?.max_position_size)} />
        <ReviewMetric label="最大投入成本" value={formatPrice(metrics?.max_capital_at_risk)} />
        <ReviewMetric label="總已實現損益" value={getSignedPriceText(metrics?.total_realized_pnl)} />
      </LifecyclePerspectiveCard>
      <LifecyclePerspectiveCard title="結案與風險處理視角">
        <ReviewMetric label="結案次數" value={formatPlainValue(exitSequence?.exit_count)} />
        <ReviewMetric label="部分結案" value={formatPlainValue(exitSequence?.partial_exit_count)} />
        <ReviewMetric label="破位後降低曝險比例" value={getSignedPercentText(exitSequence?.percentage_sold_after_breakdown)} />
        <ReviewMetric label="保護獲利" value={getSignedPriceText(exitSequence?.profit_protected_by_partial_exits)} />
      </LifecyclePerspectiveCard>
    </div>
  );
}

function LifecycleEventTimeline({ events, snapshots, evidenceItems, symbol }: { events: PositionLifecycleEventFact[]; snapshots: PositionLifecycleEventIndicatorSnapshot[]; evidenceItems: LifecycleTextItem[] | undefined; symbol: string }) {
  const snapshotByKey = new Map(snapshots.map((snapshot) => [snapshot.event_key, snapshot]));
  if (events.length === 0) {
    return <div className="rounded-xl border border-border bg-surface px-4 py-8 text-center text-sm text-text-faint">此部位尚無生命週期事件事實。</div>;
  }

  return (
    <article className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-text-primary">時間線與事件證據</h3>
      <p className="mt-1 text-xs text-text-muted">事件以 ledger 事實呈現，費用與交易稅只作為已保存事實，不要求手動補稅。</p>
      <div className="mt-3 space-y-3 border-l border-border-subtle pl-3 sm:pl-4">
        {events.map((event, index) => {
          const snapshot = snapshotByKey.get(event.event_key);
          const provenance = getLifecycleProvenance([event]);
          const eventEvidence = getLifecycleEvidenceForEvent(evidenceItems, event);
          return (
            <section key={event.event_key ?? `${event.event_date}-${index}`} className="rounded-xl border border-border bg-surface p-4 shadow-sm">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${getPositionEventTypeClass(event.event_type)}`}>
                      {formatPositionEventTypeLabel(event.event_type)}
                    </span>
                    <span className="font-mono text-sm font-semibold text-text-primary">{event.event_date ?? "日期未記錄"}</span>
                    <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${LIFECYCLE_PROVENANCE_CLASS[provenance]}`}>{provenance}</span>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-text-muted">
                    來源：{formatPositionEventSourceLabel(event.source)}。{formatPositionEventSourceHelper(event.source)}
                  </p>
                </div>
                <div className="rounded-lg border border-border-subtle bg-card px-3 py-2 text-left md:text-right">
                  <p className="text-xs text-text-faint">Event Key</p>
                  <p className="mt-1 font-mono text-sm font-medium text-text-primary">{event.event_key}</p>
                </div>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                <TimelineMetric label="價格" value={formatPrice(event.price, symbol)} />
                <TimelineMetric label="數量" value={event.quantity == null ? "—" : `${event.quantity} 股`} />
                <TimelineMetric label="手續費（ledger）" value={formatPrice(event.fees, symbol)} />
                <TimelineMetric label="交易稅（ledger）" value={formatPrice(event.taxes, symbol)} />
              </div>

              <div className="mt-3 grid gap-2 md:grid-cols-3">
                <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                  <p className="text-xs text-text-faint">原因代碼</p>
                  <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelineReasonCode(event.reason_code ?? null)}</p>
                </div>
                <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                  <p className="text-xs text-text-faint">計畫遵循</p>
                  <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelinePlanAdherence(event.plan_adherence ?? null)}</p>
                </div>
                <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                  <p className="text-xs text-text-faint">信心水準</p>
                  <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelineConfidence(event.confidence_level ?? null)}</p>
                </div>
              </div>

              {snapshot && (
                <div className="mt-3 rounded-lg border border-border-subtle bg-card px-3 py-2">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-medium text-text-muted">Point-in-time 指標 / 市場環境快照</p>
                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                      {formatMarketRegimeLabel(snapshot.market_regime ?? "insufficient_data")}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                    <TimelineMetric label="MA20" value={formatPrice(snapshot.ma20, symbol)} />
                    <TimelineMetric label="MA60" value={formatPrice(snapshot.ma60, symbol)} />
                    <TimelineMetric label="相對 MA20" value={getSignedPercentText(snapshot.event_price_vs_ma20_pct)} />
                    <TimelineMetric label="量比" value={formatPlainValue(snapshot.volume_ratio)} />
                  </div>
                </div>
              )}

              {event.data_quality_note && (
                <div className="mt-3 rounded-lg border border-border-subtle bg-card px-3 py-2">
                  <p className="text-xs text-text-faint">資料品質備註</p>
                  <p className="mt-1 text-sm leading-relaxed text-text-secondary">{event.data_quality_note}</p>
                </div>
              )}

              {eventEvidence.length > 0 && (
                <div className="mt-3 space-y-2">
                  <p className="text-xs font-medium text-text-muted">事件層級證據片段</p>
                  {eventEvidence.map((item, evidenceIndex) => <LifecycleTextItemBlock key={`${event.event_key}-evidence-${evidenceIndex}`} item={item} />)}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </article>
  );
}

function ReviewModal({ item, review, loading, error, copyStatus, onCopyEvidence, onClose }: ReviewModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);

  function handleBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    mouseDownOnBackdrop.current = event.target === backdropRef.current;
  }

  function handleBackdropClick(event: MouseEvent<HTMLDivElement>) {
    if (mouseDownOnBackdrop.current && event.target === backdropRef.current) onClose();
    mouseDownOnBackdrop.current = false;
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const result = review?.review_result;

  return (
    <div
      ref={backdropRef}
      onMouseDown={handleBackdropMouseDown}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">{item.symbol} 檢討分析</p>
            <p className="mt-1 text-xs text-text-faint">
              {item.entry_date} → {item.exit_date} ｜ 結案 {item.exit_quantity} 股 ｜ {getSignedPriceText(item.realized_pnl, item.symbol)}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
            aria-label="關閉檢討分析"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" />
              <p className="text-sm font-medium text-text-primary">載入檢討分析中</p>
              <p className="text-xs text-text-muted">若尚未產生，會建立這筆結案批次的檢討。</p>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </div>
          )}

          {review && result && (
            <>
              <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-xs font-medium text-text-muted">檢討版本</p>
                  <p className="mt-1 text-sm text-text-primary">{review.review_version}</p>
                </div>
                <button
                  type="button"
                  onClick={onCopyEvidence}
                  className="rounded-lg border border-indigo-500/40 px-4 py-2 text-sm font-medium text-indigo-700 transition hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:text-indigo-300 dark:hover:bg-indigo-950"
                >
                  {copyStatus === "success" ? "已複製指標資料" : copyStatus === "error" ? "複製失敗" : "複製指標資料"}
                </button>
              </div>

              <UserReadableConclusionCard conclusion={result.user_readable_conclusion} />
              <TradeResultSection metrics={result.trade_result} symbol={item.symbol} />
              <ReviewSectionCard title="進場檢討" section={result.entry_review} />
              <ReviewSectionCard title="持有期間檢討" section={result.holding_review} />
              <ReviewSectionCard title="結案與風險處理檢討" section={result.exit_review} />
              <ReviewSectionCard title="整體執行紀律檢討" section={result.operation_review} />
              {hasDataQualityPrompt(result.data_quality) && result.data_quality && <DataQualitySection dataQuality={result.data_quality} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function TimelineModal({ group, timeline, loading, error, onClose }: TimelineModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);

  function handleBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    mouseDownOnBackdrop.current = event.target === backdropRef.current;
  }

  function handleBackdropClick(event: MouseEvent<HTMLDivElement>) {
    if (mouseDownOnBackdrop.current && event.target === backdropRef.current) onClose();
    mouseDownOnBackdrop.current = false;
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      ref={backdropRef}
      onMouseDown={handleBackdropMouseDown}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-4xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">{group.symbol} 事件時間線</p>
            <p className="mt-1 text-xs text-text-faint">
              部位事件時間線 ｜ Group {group.position_group_id.slice(0, 8)} ｜ {group.exitBatchCount} 筆結案批次
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
            aria-label="關閉事件時間線"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-xl border border-border bg-surface p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-xs font-medium text-text-muted">事件 Ledger</p>
                <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                  此視窗只呈現部位事件時間線與資料來源，不進行生命週期檢討、績效判斷或意圖推論。
                </p>
              </div>
              <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                {timeline ? `${timeline.events.length} 筆事件` : "事件載入中"}
              </span>
            </div>
          </div>

          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" />
              <p className="text-sm font-medium text-text-primary">載入事件時間線中</p>
              <p className="text-xs text-text-muted">正在讀取這個 position group 的事件紀錄。</p>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </div>
          )}

          {timeline && timeline.events.length === 0 && !loading && (
            <div className="rounded-xl border border-border bg-surface px-4 py-8 text-center text-sm text-text-faint">
              此部位尚無事件時間線紀錄。
            </div>
          )}

          {timeline && timeline.events.length > 0 && (
            <div className="space-y-3 border-l border-border-subtle pl-3 sm:pl-4">
              {timeline.events.map((timelineEvent) => {
                const decisionContextInsufficient = hasInsufficientDecisionContext(timelineEvent);
                return (
                  <article key={timelineEvent.id} className="rounded-xl border border-border bg-surface p-4 shadow-sm">
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${POSITION_EVENT_TYPE_CLASS[timelineEvent.event_type]}`}>
                            {POSITION_EVENT_TYPE_LABEL[timelineEvent.event_type]}
                          </span>
                          <span className="font-mono text-sm font-semibold text-text-primary">{timelineEvent.event_date}</span>
                          <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                            Event #{timelineEvent.id}
                          </span>
                        </div>
                        <p className="mt-2 text-xs leading-relaxed text-text-muted">
                          來源：{POSITION_EVENT_SOURCE_LABEL[timelineEvent.source]}。{POSITION_EVENT_SOURCE_HELPER[timelineEvent.source]}
                        </p>
                      </div>
                      <div className="rounded-lg border border-border-subtle bg-card px-3 py-2 text-left md:text-right">
                        <p className="text-xs text-text-faint">來源 Portfolio ID</p>
                        <p className="mt-1 font-mono text-sm font-medium text-text-primary">{formatPlainValue(timelineEvent.source_portfolio_id)}</p>
                      </div>
                    </div>

                    <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
                      <TimelineMetric label="價格" value={formatPrice(timelineEvent.price, timelineEvent.symbol)} />
                      <TimelineMetric label="數量" value={`${timelineEvent.quantity} 股`} />
                      <TimelineMetric label="手續費（系統計算/已保存）" value={formatPrice(timelineEvent.fees, timelineEvent.symbol)} />
                      <TimelineMetric label="交易稅（系統計算/已保存）" value={formatPrice(timelineEvent.taxes, timelineEvent.symbol)} />
                    </div>

                    <div className="mt-3 grid gap-2 md:grid-cols-3">
                      <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                        <p className="text-xs text-text-faint">原因分類</p>
                        <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelineReasonCategory(timelineEvent.reason_category)}</p>
                      </div>
                      <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                        <p className="text-xs text-text-faint">原因代碼</p>
                        <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelineReasonCode(timelineEvent.reason_code)}</p>
                      </div>
                      <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                        <p className="text-xs text-text-faint">信心水準</p>
                        <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelineConfidence(timelineEvent.confidence_level)}</p>
                      </div>
                    </div>

                    <div className="mt-3 rounded-lg border border-border-subtle bg-card px-3 py-2">
                      <p className="text-xs text-text-faint">計畫遵循</p>
                      <p className="mt-1 text-sm font-medium text-text-primary">{formatTimelinePlanAdherence(timelineEvent.plan_adherence)}</p>
                    </div>

                    {decisionContextInsufficient && (
                      <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                        決策脈絡不足：計畫遵循、原因代碼或信心水準未完整記錄。本時間線只顯示已保存事件，不推論當時意圖。
                      </div>
                    )}

                    {(timelineEvent.note || timelineEvent.data_quality_note) && (
                      <div className="mt-3 space-y-2">
                        {timelineEvent.note && (
                          <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                            <p className="text-xs text-text-faint">備註</p>
                            <p className="mt-1 text-sm leading-relaxed text-text-secondary">{timelineEvent.note}</p>
                          </div>
                        )}
                        {timelineEvent.data_quality_note && (
                          <div className="rounded-lg border border-border-subtle bg-card px-3 py-2">
                            <p className="text-xs text-text-faint">資料品質備註</p>
                            <p className="mt-1 text-sm leading-relaxed text-text-secondary">{timelineEvent.data_quality_note}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function LifecycleReviewModal({ group, review, loading, error, copyStatus, onCopyEvidence, onClose }: LifecycleReviewModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const mouseDownOnBackdrop = useRef(false);

  function handleBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    mouseDownOnBackdrop.current = event.target === backdropRef.current;
  }

  function handleBackdropClick(event: MouseEvent<HTMLDivElement>) {
    if (mouseDownOnBackdrop.current && event.target === backdropRef.current) onClose();
    mouseDownOnBackdrop.current = false;
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const result = review?.review_result;
  const lifecycleReview = result?.lifecycle_review;
  const eventFacts = result?.event_facts ?? [];
  const snapshots = result?.event_indicator_snapshots ?? [];
  const provenance = getLifecycleProvenance(eventFacts);
  const hasDecisionWarning = hasLifecycleDecisionContextWarning(review);
  const hasBackfilledCaveat = hasBackfilledLifecyclePlanCaveat(review);

  return (
    <div
      ref={backdropRef}
      onMouseDown={handleBackdropMouseDown}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center"
    >
      <div className="max-h-[85vh] w-full max-w-5xl overflow-y-auto rounded-2xl bg-card shadow-xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border-subtle bg-card px-5 py-4">
          <div>
            <p className="font-semibold text-text-primary">{group.symbol} 整體部位檢討</p>
            <p className="mt-1 text-xs text-text-faint">
              Whole lifecycle review ｜ Group {group.position_group_id.slice(0, 8)} ｜ 多次進場/新增批次/分批降低曝險整體檢討
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-faint hover:bg-card-hover hover:text-text-secondary"
            aria-label="關閉整體部位檢討"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="space-y-4 p-5">
          <div className="rounded-xl border border-border bg-surface p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-xs font-medium text-text-muted">整體部位生命週期檢討</p>
                <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                  此視窗檢討整個 position group 的進場序列、持有路徑、部位管理與結案序列；每一列結案批次的「檢討分析」仍是 Single Trade Review。
                </p>
              </div>
              <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${LIFECYCLE_PROVENANCE_CLASS[provenance]}`}>
                {provenance}
              </span>
            </div>
          </div>

          {loading && (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
              <div className="h-10 w-10 animate-spin rounded-full border-4 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" />
              <p className="text-sm font-medium text-text-primary">載入整體部位檢討中</p>
              <p className="text-xs text-text-muted">若尚未產生，會建立這個 position group 的 lifecycle review。</p>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </div>
          )}

          {review && result && (
            <>
              <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-xs font-medium text-text-muted">檢討版本</p>
                  <p className="mt-1 text-sm text-text-primary">{review.review_version}</p>
                </div>
                <button
                  type="button"
                  onClick={onCopyEvidence}
                  className="rounded-lg border border-indigo-500/40 px-4 py-2 text-sm font-medium text-indigo-700 transition hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:text-indigo-300 dark:hover:bg-indigo-950"
                >
                  {copyStatus === "success" ? "已複製生命週期證據" : copyStatus === "error" ? "複製失敗" : "複製生命週期證據"}
                </button>
              </div>

              {hasDecisionWarning && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 shadow-sm dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                  決策脈絡不足：decision_context 未完整存在，或分類包含資料不足。此檢討只使用已保存事件、ledger 費稅與 point-in-time 指標，不推論未記錄意圖。
                </div>
              )}

              {hasBackfilledCaveat && (
                <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800 shadow-sm dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300">
                  事後補填 plan caveat：此 lifecycle review 使用了使用者事後補填的 operation plan。它可改善檢討脈絡，但不代表原始進場當下已存在同一份計畫。
                </div>
              )}

              <LifecyclePlanReviewRelationSection review={review} />
              <LifecycleOverallSection review={review} />
              <LifecyclePerspectives metrics={result.lifecycle_metrics} entrySequence={result.entry_sequence} exitSequence={result.exit_sequence} />
              <div className="grid gap-3 md:grid-cols-2">
                <LifecycleTextItemList label="做得好的地方" items={lifecycleReview?.what_worked} />
                <LifecycleTextItemList label="需要檢討的地方" items={lifecycleReview?.what_needs_review} />
              </div>
              <LifecycleTextItemList label="下次紀律規則" items={lifecycleReview?.next_operation_rules} />
              <LifecycleEventTimeline events={eventFacts} snapshots={snapshots} evidenceItems={lifecycleReview?.event_level_evidence} symbol={review.symbol} />
              <LifecycleDataQualitySection dataQuality={result.data_quality} notes={lifecycleReview?.data_quality_notes} />
              {result.advanced_internal && (
                <details className="rounded-xl border border-border bg-card p-4 shadow-sm">
                  <summary className="cursor-pointer text-sm font-semibold text-text-primary">進階 trace（內部指標與分數）</summary>
                  <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-border-subtle bg-surface p-3 text-xs leading-relaxed text-text-secondary">
                    {JSON.stringify(result.advanced_internal, null, 2)}
                  </pre>
                </details>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ClosedPortfolioPage() {
  const [items, setItems] = useState<ClosedPortfolioItem[]>([]);
  const [selectedPeriod, setSelectedPeriod] = useState<PeriodKey>(readStoredPeriod);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedReviewItem, setSelectedReviewItem] = useState<ClosedPortfolioItem | null>(null);
  const [reviewMap, setReviewMap] = useState<Record<number, TradeReviewResponse>>({});
  const [reviewLoading, setReviewLoading] = useState<Record<number, boolean>>({});
  const [reviewError, setReviewError] = useState<Record<number, string | null>>({});
  const [copyStatus, setCopyStatus] = useState<Record<number, CopyStatus>>({});
  const [selectedTimelineGroup, setSelectedTimelineGroup] = useState<ClosedPortfolioGroup | null>(null);
  const [timelineMap, setTimelineMap] = useState<Record<string, PositionGroupEventsResponse>>({});
  const [timelineLoading, setTimelineLoading] = useState<Record<string, boolean>>({});
  const [timelineError, setTimelineError] = useState<Record<string, string | null>>({});
  const [selectedLifecycleGroup, setSelectedLifecycleGroup] = useState<ClosedPortfolioGroup | null>(null);
  const [lifecycleReviewMap, setLifecycleReviewMap] = useState<Record<string, PositionLifecycleReviewResponse>>({});
  const [lifecycleReviewLoading, setLifecycleReviewLoading] = useState<Record<string, boolean>>({});
  const [lifecycleReviewError, setLifecycleReviewError] = useState<Record<string, string | null>>({});
  const [lifecycleCopyStatus, setLifecycleCopyStatus] = useState<Record<string, CopyStatus>>({});

  useEffect(() => {
    writeStoredPeriod(selectedPeriod);
  }, [selectedPeriod]);

  useEffect(() => {
    let cancelled = false;

    async function loadClosedPortfolio() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchClosedPortfolioItems();
        if (!cancelled) setItems(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "已結案持股載入失敗");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadClosedPortfolio();
    return () => {
      cancelled = true;
    };
  }, []);

  const activePeriod = PERIOD_OPTIONS.find((option) => option.key === selectedPeriod) ?? PERIOD_OPTIONS[2];
  const filteredItems = useMemo(() => {
    const today = getToday();
    return items.filter((item) => isWithinPeriod(item.exit_date, activePeriod.days, today));
  }, [activePeriod.days, items]);

  const groupedItems = useMemo(() => groupClosedItems(filteredItems), [filteredItems]);

  const totalRealizedPnl = useMemo(
    () => filteredItems.reduce((total, item) => total + item.realized_pnl, 0),
    [filteredItems],
  );
  const totalClass = totalRealizedPnl >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";

  async function fetchReview(item: ClosedPortfolioItem): Promise<TradeReviewResponse> {
    return fetchOrCreateTradeReview(item.id);
  }

  async function openReview(item: ClosedPortfolioItem): Promise<void> {
    setSelectedReviewItem(item);
    setCopyStatus((prev) => ({ ...prev, [item.id]: "idle" }));
    if (reviewMap[item.id]) return;

    setAsyncMapValue(setReviewLoading, item.id, true);
    setAsyncMapValue(setReviewError, item.id, null);
    try {
      const review = await fetchReview(item);
      setAsyncMapValue(setReviewMap, item.id, review);
    } catch (err) {
      setAsyncMapValue(setReviewError, item.id, err instanceof Error ? err.message : "檢討分析載入失敗");
    } finally {
      setAsyncMapValue(setReviewLoading, item.id, false);
    }
  }

  async function fetchTimeline(positionGroupId: string): Promise<PositionGroupEventsResponse> {
    return fetchPositionGroupEvents(positionGroupId);
  }

  async function openTimeline(group: ClosedPortfolioGroup): Promise<void> {
    setSelectedTimelineGroup(group);
    if (timelineMap[group.position_group_id]) return;

    setAsyncMapValue(setTimelineLoading, group.position_group_id, true);
    setAsyncMapValue(setTimelineError, group.position_group_id, null);
    try {
      const timeline = await fetchTimeline(group.position_group_id);
      setAsyncMapValue(setTimelineMap, group.position_group_id, timeline);
    } catch (err) {
      setAsyncMapValue(setTimelineError, group.position_group_id, err instanceof Error ? err.message : "事件時間線載入失敗");
    } finally {
      setAsyncMapValue(setTimelineLoading, group.position_group_id, false);
    }
  }

  async function fetchLifecycleReview(positionGroupId: string): Promise<PositionLifecycleReviewResponse> {
    return createPositionLifecycleReview(positionGroupId);
  }

  async function openLifecycleReview(group: ClosedPortfolioGroup): Promise<void> {
    setSelectedLifecycleGroup(group);
    setLifecycleCopyStatus((prev) => ({ ...prev, [group.position_group_id]: "idle" }));

    setAsyncMapValue(setLifecycleReviewLoading, group.position_group_id, true);
    setAsyncMapValue(setLifecycleReviewError, group.position_group_id, null);
    try {
      const review = await fetchLifecycleReview(group.position_group_id);
      setAsyncMapValue(setLifecycleReviewMap, group.position_group_id, review);
    } catch (err) {
      setAsyncMapValue(setLifecycleReviewError, group.position_group_id, err instanceof Error ? err.message : "整體部位檢討載入失敗");
    } finally {
      setAsyncMapValue(setLifecycleReviewLoading, group.position_group_id, false);
    }
  }

  async function copyEvidence(): Promise<void> {
    if (!selectedReviewItem) return;
    const review = reviewMap[selectedReviewItem.id];
    if (!review) return;

    try {
      await navigator.clipboard.writeText(JSON.stringify(review.evidence_payload, null, 2));
      setCopyStatus((prev) => ({ ...prev, [selectedReviewItem.id]: "success" }));
    } catch {
      setCopyStatus((prev) => ({ ...prev, [selectedReviewItem.id]: "error" }));
    }
  }

  async function copyLifecycleEvidence(): Promise<void> {
    if (!selectedLifecycleGroup) return;
    const review = lifecycleReviewMap[selectedLifecycleGroup.position_group_id];
    if (!review) return;

    try {
      await navigator.clipboard.writeText(JSON.stringify(review.evidence_payload, null, 2));
      setLifecycleCopyStatus((prev) => ({ ...prev, [selectedLifecycleGroup.position_group_id]: "success" }));
    } catch {
      setLifecycleCopyStatus((prev) => ({ ...prev, [selectedLifecycleGroup.position_group_id]: "error" }));
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="h-3 w-20 animate-pulse rounded bg-border" />
          <div className="mt-3 h-8 w-40 animate-pulse rounded bg-border" />
        </div>
        {[1, 2].map((item) => (
          <div key={item} className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm">
            <div className="h-5 w-24 animate-pulse rounded bg-border" />
            <div className="mt-2 h-3 w-48 animate-pulse rounded bg-border" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-xs font-medium text-text-faint">{activePeriod.label} 已實現損益</p>
              <p className={`mt-1 font-mono text-3xl font-semibold ${totalClass}`}>
                {totalRealizedPnl >= 0 ? "+" : ""}{formatPrice(totalRealizedPnl)}
              </p>
              <p className="mt-1 text-xs text-text-muted">篩選 {filteredItems.length} 筆已結案紀錄</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {PERIOD_OPTIONS.map((option) => {
                const isActive = option.key === selectedPeriod;
                return (
                  <button
                    key={option.key}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setSelectedPeriod(option.key)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${isActive
                      ? "bg-indigo-600 text-white"
                      : "border border-border bg-card text-text-muted hover:bg-card-hover"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-sm dark:border-red-900 dark:bg-red-950 dark:text-red-400">
            {error}
          </div>
        )}

        <section className="rounded-xl border border-border bg-card shadow-sm">
          <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
            <h2 className="text-sm font-semibold text-text-primary">已結案持股</h2>
            <span className="text-xs text-text-faint">共 {filteredItems.length} 筆</span>
          </div>

          {filteredItems.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-text-faint">
              此期間尚無已結案持股。
            </div>
          ) : (
            <div className="space-y-4 p-4">
              {groupedItems.map((group) => {
                const groupIsProfit = group.totalRealizedPnl >= 0;
                const groupResultClass = groupIsProfit ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
                const isTimelineLoading = timelineLoading[group.position_group_id] ?? false;
                const isLifecycleReviewLoading = lifecycleReviewLoading[group.position_group_id] ?? false;
                return (
                  <article key={group.position_group_id} className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
                    <div className="border-b border-border bg-surface px-4 py-4 sm:px-5">
                      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                        <div className="border-l-4 border-l-indigo-500 pl-3 dark:border-l-indigo-400">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-mono text-lg font-semibold text-text-primary">{group.symbol}</p>
                            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                              進場 {group.entry_date}
                            </span>
                            <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                              成本 {formatPrice(group.entry_price, group.symbol)}
                            </span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-2 text-xs text-text-muted">
                            <span className="rounded-md border border-border-subtle bg-card px-2 py-1">總結案 {group.totalClosedQuantity} 股</span>
                            <span className="rounded-md border border-border-subtle bg-card px-2 py-1">結案批次 {group.exitBatchCount} 筆</span>
                            <span className="rounded-md border border-border-subtle bg-card px-2 py-1 font-mono text-text-faint">
                              Group {group.position_group_id.slice(0, 8)}
                            </span>
                          </div>
                        </div>
                        <div className="flex flex-col gap-2 md:items-end">
                          <div className="rounded-xl border border-border bg-card px-4 py-3 text-left shadow-sm md:text-right">
                            <p className="text-xs font-medium text-text-muted">股票總已實現損益</p>
                            <p className={`mt-1 font-mono text-lg font-semibold ${groupResultClass}`}>{getSignedPriceText(group.totalRealizedPnl, group.symbol)}</p>
                          </div>
                          <div className="flex flex-wrap gap-2 md:justify-end">
                            <button
                              type="button"
                              onClick={() => void openLifecycleReview(group)}
                              disabled={isLifecycleReviewLoading}
                              className="rounded-lg border border-indigo-500/40 px-3 py-2 text-xs font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-indigo-300 dark:hover:bg-indigo-950"
                            >
                              {isLifecycleReviewLoading ? "載入整體檢討…" : "整體部位檢討"}
                            </button>
                            <button
                              type="button"
                              onClick={() => void openTimeline(group)}
                              disabled={isTimelineLoading}
                              className="rounded-lg border border-blue-500/40 bg-card px-3 py-2 text-xs font-medium text-blue-700 transition hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-blue-300 dark:hover:bg-blue-950"
                            >
                          {isTimelineLoading ? "載入時間線…" : "事件時間線"}
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="bg-card p-3 sm:p-4">
                      <div className="space-y-2 border-l border-border-subtle pl-3 sm:pl-4">
                        {group.items.map((item) => {
                          const isProfit = item.realized_pnl >= 0;
                          const resultClass = isProfit ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400";
                          const isReviewLoading = reviewLoading[item.id] ?? false;
                          return (
                            <div key={item.id} className="rounded-lg border border-border-subtle bg-surface px-3 py-3 shadow-sm sm:px-4">
                              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <p className="font-semibold text-text-primary">結案批次 #{item.id}</p>
                                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                                      {item.entry_date} → {item.exit_date}
                                    </span>
                                    <span className="rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs text-badge-neutral-text">
                                      持有 {item.holding_days} 天
                                    </span>
                                  </div>
                                  <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs text-text-muted">
                                    <span>{formatPrice(item.entry_price, item.symbol)} → {formatPrice(item.exit_price, item.symbol)}</span>
                                    <span>結案 {item.exit_quantity} 股</span>
                                    <span>費稅 {formatPrice(item.exit_fees + item.exit_taxes, item.symbol)}</span>
                                  </div>
                                </div>
                                <div className="flex items-center justify-between gap-3 sm:justify-end">
                                  <div className="text-left sm:text-right">
                                    <p className={`font-mono text-sm font-semibold ${resultClass}`}>{getSignedPriceText(item.realized_pnl, item.symbol)}</p>
                                    <p className={`font-mono text-xs ${resultClass}`}>{getSignedPercentText(item.realized_return_pct)}</p>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => void openReview(item)}
                                    disabled={isReviewLoading}
                                    className="rounded-lg border border-indigo-500/40 px-3 py-2 text-xs font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-indigo-300 dark:hover:bg-indigo-950"
                                  >
                                    {isReviewLoading ? "載入中…" : "檢討分析"}
                                  </button>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>

      {selectedReviewItem && (
        <ReviewModal
          item={selectedReviewItem}
          review={reviewMap[selectedReviewItem.id] ?? null}
          loading={reviewLoading[selectedReviewItem.id] ?? false}
          error={reviewError[selectedReviewItem.id] ?? null}
          copyStatus={copyStatus[selectedReviewItem.id] ?? "idle"}
          onCopyEvidence={() => void copyEvidence()}
          onClose={() => setSelectedReviewItem(null)}
        />
      )}

      {selectedTimelineGroup && (
        <TimelineModal
          group={selectedTimelineGroup}
          timeline={timelineMap[selectedTimelineGroup.position_group_id] ?? null}
          loading={timelineLoading[selectedTimelineGroup.position_group_id] ?? false}
          error={timelineError[selectedTimelineGroup.position_group_id] ?? null}
          onClose={() => setSelectedTimelineGroup(null)}
        />
      )}

      {selectedLifecycleGroup && (
        <LifecycleReviewModal
          group={selectedLifecycleGroup}
          review={lifecycleReviewMap[selectedLifecycleGroup.position_group_id] ?? null}
          loading={lifecycleReviewLoading[selectedLifecycleGroup.position_group_id] ?? false}
          error={lifecycleReviewError[selectedLifecycleGroup.position_group_id] ?? null}
          copyStatus={lifecycleCopyStatus[selectedLifecycleGroup.position_group_id] ?? "idle"}
          onCopyEvidence={() => void copyLifecycleEvidence()}
          onClose={() => setSelectedLifecycleGroup(null)}
        />
      )}
    </>
  );
}
