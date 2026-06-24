import {
  ADD_ENTRY_CONDITION_VALUES,
  DEFAULT_STOP_RULE_VALUES,
  ENTRY_RECORD_REASON_VALUES,
  LIFECYCLE_SETUP_TYPE_VALUES,
  PLANNED_HOLDING_PERIOD_VALUES,
  type AddEntryCondition,
  type AddEntryReasonCode,
  type DecisionConfidenceLevel,
  type DefaultStopRule,
  type EntryRecordReason,
  type LifecycleSetupType,
  type PlanAdherence,
  type PlannedHoldingPeriod,
} from "./portfolioTypes";

export const ENTRY_RECORD_REASON_LABEL = {
  breakout_confirmation: "突破確認",
  pullback_held_support: "回測守住支撐",
  pullback_held_ma20: "回測守住 20 日線",
  institutional_flow_strengthened: "法人籌碼轉強",
  fundamental_thesis_improved: "基本面假設改善",
  event_or_news_catalyst: "事件／消息催化",
  long_term_accumulation: "長期分批佈局",
  value_revaluation: "價值重估",
  other: "其他固定理由",
  not_recorded: "未記錄",
} satisfies Record<EntryRecordReason, string>;

export const PLANNED_HOLDING_PERIOD_LABEL = {
  short_term: "短線（數日內）",
  swing: "波段（數週）",
  medium_term: "中期（數月）",
  long_term: "長期（半年以上）",
  not_recorded: "未記錄",
} satisfies Record<PlannedHoldingPeriod, string>;

export const DEFAULT_STOP_RULE_LABEL = {
  break_20d_low: "跌破 20 日低點",
  break_ma20: "跌破 20 日線",
  break_ma60: "跌破 60 日線",
  cost_minus_pct: "成本下方固定百分比",
  fixed_price: "固定防守價",
  no_stop_recorded: "未設定防守",
  not_recorded: "未記錄",
} satisfies Record<DefaultStopRule, string>;

export const ADD_ENTRY_CONDITION_LABEL = {
  no_add_entry: "不新增批次",
  breakout_above_prior_high: "突破前高再新增批次",
  pullback_holds_ma20: "回測守住 20 日線",
  pullback_holds_support: "回測守住支撐",
  institutional_flow_continues: "法人籌碼延續",
  profit_threshold_reached: "達成獲利門檻",
  data_quality_complete_only: "資料完整才新增批次",
  no_averaging_down: "不攤平",
  custom_plan_required: "需另訂自訂計畫",
  not_recorded: "未記錄",
} satisfies Record<AddEntryCondition, string>;

export const ADD_ENTRY_REASON_CODE_LABEL = {
  breakout_confirmation: "突破確認",
  pullback_held_support: "回測守住支撐",
  pullback_held_ma20: "回測守住 20 日線",
  institutional_flow_strengthened: "法人籌碼轉強",
  fundamental_thesis_improved: "基本面假設改善",
  event_or_news_catalyst: "事件／消息催化",
  long_term_accumulation: "長期分批佈局",
  value_revaluation: "價值重估",
  other: "其他固定理由",
  planned_scale_in: "依原計畫新增批次",
  averaging_down: "向下攤平新增批次",
  chasing_momentum: "追價動能",
  not_recorded: "未記錄",
} satisfies Record<AddEntryReasonCode, string>;

export const PLAN_ADHERENCE_LABEL = {
  yes: "符合原始計畫",
  partial: "部分符合原始計畫",
  no: "否，記錄為違反原始新增批次計畫",
  not_recorded: "未記錄",
} satisfies Record<PlanAdherence, string>;

export const CONFIDENCE_LEVEL_LABEL = {
  high: "高",
  medium: "中",
  low: "低",
  not_recorded: "未記錄",
} satisfies Record<DecisionConfidenceLevel, string>;

export const LIFECYCLE_SETUP_TYPE_LABEL = {
  breakout: "突破型",
  pullback: "回測型",
  mean_reversion: "均值回歸",
  value_revaluation: "價值重估",
  earnings_or_event: "財報／事件驅動",
  momentum_continuation: "動能延續",
  long_term_accumulation: "長期分批佈局",
  defensive_rebalance: "防守再平衡",
  other: "其他",
} satisfies Record<LifecycleSetupType, string>;

export const OPERATION_PLAN_STATUS_LABEL = {
  missing: "缺少 operation plan",
  present: "原始計畫已記錄",
  backfilled: "已事後補填",
} as const;

export const OPERATION_PLAN_STATUS_CLASS = {
  missing: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300",
  present:
    "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  backfilled: "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
} as const;

export const PLANNED_HOLDING_PERIOD_OPTIONS = PLANNED_HOLDING_PERIOD_VALUES.map((value) => ({
  value,
  label: PLANNED_HOLDING_PERIOD_LABEL[value],
}));

export const ENTRY_RECORD_REASON_OPTIONS = ENTRY_RECORD_REASON_VALUES.map((value) => ({
  value,
  label: ENTRY_RECORD_REASON_LABEL[value],
}));

export const DEFAULT_STOP_RULE_OPTIONS = DEFAULT_STOP_RULE_VALUES.map((value) => ({
  value,
  label: DEFAULT_STOP_RULE_LABEL[value],
}));

export const ADD_ENTRY_CONDITION_OPTIONS = ADD_ENTRY_CONDITION_VALUES.map((value) => ({
  value,
  label: ADD_ENTRY_CONDITION_LABEL[value],
}));

export const LIFECYCLE_SETUP_TYPE_OPTIONS = LIFECYCLE_SETUP_TYPE_VALUES.map((value) => ({
  value,
  label: LIFECYCLE_SETUP_TYPE_LABEL[value],
}));
