import type { AnalyzeResponse, ChipStabilityContext, Phase1Observation, TechnicalIndicators } from "./analysisTypes";
import { formatPrice, formatVolume } from "./formatters";

export type CopyStatus = "idle" | "success" | "error";

export const COPY_STATUS_RESET_MS = 1800;

const BOLLINGER_POSITION_LABEL: Record<string, { label: string }> = {
  near_upper: { label: "接近上軌" },
  above_mid: { label: "中軌上方" },
  below_mid: { label: "中軌下方" },
  near_lower: { label: "接近下軌" },
  flat: { label: "通道平坦" },
};

const MACD_BIAS_LABEL: Record<string, { label: string }> = {
  bullish: { label: "多方動能" },
  bearish: { label: "空方動能" },
  neutral: { label: "中性" },
};

const KD_SIGNAL_LABEL: Record<string, { label: string }> = {
  bullish_cross: { label: "黃金交叉" },
  bearish_cross: { label: "死亡交叉" },
  neutral: { label: "中性" },
};

const KD_ZONE_LABEL: Record<string, { label: string }> = {
  oversold: { label: "低檔區" },
  overbought: { label: "高檔區" },
  neutral: { label: "中性區" },
};

const ADX_STRENGTH_LABEL: Record<string, { label: string }> = {
  strong: { label: "趨勢明確" },
  neutral: { label: "趨勢中等" },
  weak: { label: "趨勢偏弱" },
};

const ADX_DIRECTION_LABEL: Record<string, { label: string }> = {
  bullish: { label: "多方趨勢" },
  bearish: { label: "空方趨勢" },
  neutral: { label: "中性" },
};

const OBV_SIGNAL_LABEL: Record<string, { label: string }> = {
  price_volume_confirm: { label: "量價確認" },
  bearish_divergence: { label: "量價背離" },
  bullish_divergence: { label: "低檔承接" },
  price_volume_weak: { label: "量價轉弱" },
  neutral: { label: "中性" },
};

const OBV_TREND_LABEL: Record<string, { label: string }> = {
  rising: { label: "上升" },
  falling: { label: "下降" },
  flat: { label: "盤整" },
};

const VOLATILITY_LEVEL_LABEL: Record<string, { label: string }> = {
  high: { label: "高波動" },
  medium: { label: "中波動" },
  low: { label: "低波動" },
  unknown: { label: "未知" },
};

const MFI_SIGNAL_LABEL: Record<string, { label: string }> = {
  overbought: { label: "資金過熱" },
  oversold: { label: "資金低檔" },
  bullish_flow: { label: "資金偏多" },
  bearish_flow: { label: "資金偏弱" },
  neutral: { label: "中性" },
};

const DONCHIAN_POSITION_LABEL: Record<string, { label: string }> = {
  breakout_up: { label: "突破上緣" },
  breakdown_down: { label: "跌破下緣" },
  near_upper: { label: "接近上緣" },
  near_lower: { label: "接近下緣" },
  upper_half: { label: "區間上半" },
  lower_half: { label: "區間下半" },
  flat: { label: "區間平坦" },
};

const PHASE1_ANCHOR_ORDER = ["swing_low_60d", "breakout_20d", "high_volume_60d", "entry"] as const;

const PHASE1_ANCHOR_LABEL: Record<string, string> = {
  swing_low_60d: "60 日波段低點 AVWAP",
  breakout_20d: "20 日突破 AVWAP",
  high_volume_60d: "60 日大量 AVWAP",
  entry: "持股進場日 AVWAP",
};

const PHASE1_MISSING_REASON_LABEL: Record<string, string> = {
  not_in_phase1_universe: "不在試驗版管理範圍",
  phase1_snapshot_missing: "尚無試驗版快照",
  phase1_snapshot_stale: "試驗版快照已過期",
  phase1_snapshot_read_failed: "試驗版快照讀取失敗",
};

const TECHNICAL_LABELS = {
  bollinger_position: BOLLINGER_POSITION_LABEL,
  macd_bias: MACD_BIAS_LABEL,
  kd_signal: KD_SIGNAL_LABEL,
  kd_zone: KD_ZONE_LABEL,
  adx_trend_strength: ADX_STRENGTH_LABEL,
  adx_trend_direction: ADX_DIRECTION_LABEL,
  obv_signal: OBV_SIGNAL_LABEL,
  obv_trend: OBV_TREND_LABEL,
  volatility_level: VOLATILITY_LEVEL_LABEL,
  mfi_signal: MFI_SIGNAL_LABEL,
  donchian_position: DONCHIAN_POSITION_LABEL,
} as const;

export type TechnicalLabelKind = keyof typeof TECHNICAL_LABELS;

export function formatIndicatorNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function getTechnicalIndicatorLabel(
  kind: TechnicalLabelKind,
  value: string | null | undefined,
  emptyLabel = "—",
): string {
  if (!value) return emptyLabel;
  return TECHNICAL_LABELS[kind][value]?.label ?? value;
}

export function getAnalyzeSymbolName(result: AnalyzeResponse | null, snapshot: Record<string, unknown>): string | null {
  if (typeof result?.symbol_name === "string" && result.symbol_name.trim()) return result.symbol_name.trim();
  if (typeof snapshot.name === "string" && snapshot.name.trim()) return snapshot.name.trim();
  return null;
}

export function formatMovingAverages(indicators: TechnicalIndicators, snapshotSymbol?: string): string {
  return indicators.ma5 != null || indicators.ma20 != null || indicators.ma60 != null
    ? `${formatPrice(indicators.ma5, snapshotSymbol)} / ${formatPrice(indicators.ma20, snapshotSymbol)} / ${formatPrice(indicators.ma60, snapshotSymbol)}`
    : "—";
}

function formatPhase1Distance(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function calculatePhase1DistanceFromPrice(
  price: number | null | undefined,
  reference: number | null | undefined,
): number | null {
  if (price == null || reference == null || Number.isNaN(price) || Number.isNaN(reference) || reference === 0) {
    return null;
  }
  return (price - reference) / reference * 100;
}

function formatPhase1MissingReason(reason: string | null | undefined): string {
  if (!reason) return "資料不足";
  return PHASE1_MISSING_REASON_LABEL[reason] ?? reason;
}

function buildPhase1AvwapCopyRows(
  observation: Phase1Observation | null | undefined,
  snapshotSymbol?: string,
  currentPrice?: number | null,
): Array<[string, string]> {
  if (!observation) return [];

  const entries = Object.entries(observation.anchors ?? {}).filter(([, anchor]) => anchor.available !== false);
  const priority: Map<string, number> = new Map(PHASE1_ANCHOR_ORDER.map((key, index) => [key, index]));
  const anchorRows: Array<[string, string]> = entries
    .sort(([left], [right]) => (priority.get(left) ?? 99) - (priority.get(right) ?? 99) || left.localeCompare(right))
    .map(([key, anchor]) => {
      const distance = calculatePhase1DistanceFromPrice(currentPrice, anchor.avwap) ?? anchor.distance_to_avwap_pct;
      const parts = [
        formatPrice(anchor.avwap, snapshotSymbol),
        `距離 ${formatPhase1Distance(distance)}`,
      ];
      if (anchor.anchor_date) parts.push(`錨點日 ${anchor.anchor_date}`);
      if (anchor.estimated) parts.push("日資料估算");
      return [PHASE1_ANCHOR_LABEL[key] ?? `${key} AVWAP`, parts.join(" / ")];
    });

  if (anchorRows.length > 0) {
    return [
      ["AVWAP 資料日", observation.data_date],
      ...anchorRows,
    ];
  }

  if (!observation.missing_reason) return [];
  return [
    ["AVWAP 資料日", observation.data_date],
    ["AVWAP 狀態", formatPhase1MissingReason(observation.missing_reason)],
  ];
}

function formatSignedDelta(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)} pp`;
}

function formatRatioPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(2)}%`;
}

function buildChipStabilityHistoryRows(context: ChipStabilityContext): Array<[string, string]> {
  const history = context.weekly_history ?? context.history ?? [];
  return history.slice(0, 5).map((entry) => {
    const dateLabel = entry.as_of_date ?? "—";
    const ratio = formatRatioPct(entry.thousand_lot_holder_ratio);
    const delta = formatSignedDelta(entry.thousand_lot_holder_ratio_delta_pp);
    return [`千張大戶週資料 ${dateLabel}`, `${ratio} / 週變化 ${delta}`];
  });
}

function buildChipStabilityCopyRows(
  context: ChipStabilityContext | null | undefined,
): Array<[string, string]> {
  if (!context) return [];
  return [
    ["千張大戶持股比例", `${formatRatioPct(context.thousand_lot_holder_ratio)}${context.as_of_date ? `（${context.as_of_date}）` : ""}`],
    ["較上週變化", formatSignedDelta(context.thousand_lot_holder_ratio_delta_pp)],
    ...buildChipStabilityHistoryRows(context),
  ];
}

export function buildTechnicalIndicatorsCopyText(result: AnalyzeResponse, snapshot: Record<string, unknown>): string {
  const indicators = result.technical_indicators;
  const snapshotSymbol = typeof snapshot.symbol === "string" ? snapshot.symbol : undefined;
  const displaySymbol = snapshotSymbol ?? "—";
  const symbolName = getAnalyzeSymbolName(result, snapshot);
  const marketSessionLabel = result.is_final === false ? "盤中" : "收盤";
  const currentPrice = typeof snapshot.current_price === "number" ? snapshot.current_price : null;
  const price = (value: number | null | undefined) => formatPrice(value, snapshotSymbol);
  const pricePair = (first: number | null | undefined, second: number | null | undefined, emptyLabel = "—") =>
    first != null || second != null ? `${price(first)} / ${price(second)}` : emptyLabel;
  const indicatorPair = (
    first: number | null | undefined,
    firstDigits: number,
    second: number | null | undefined,
    secondDigits = firstDigits,
    suffix = "",
    emptyLabel = "—",
  ) =>
    first != null || second != null
      ? `${formatIndicatorNumber(first, firstDigits)} / ${formatIndicatorNumber(second, secondDigits)}${suffix}`
      : emptyLabel;

  if (!indicators) {
    return [
      "技術指標摘要",
      `股票名稱：${symbolName ?? "—"}`,
      `股票代碼：${displaySymbol}`,
      `資料狀態：${marketSessionLabel}`,
      "技術指標：資料不足",
      ...buildPhase1AvwapCopyRows(result.phase1_observation, snapshotSymbol, currentPrice).map(([label, value]) => `${label}：${value}`),
      ...buildChipStabilityCopyRows(result.chip_stability_context).map(([label, value]) => `${label}：${value}`),
    ].join("\n");
  }

  const rows: Array<[string, string]> = [
    ["股票名稱", symbolName ?? "—"],
    ["股票代碼", displaySymbol],
    ["資料狀態", marketSessionLabel],
    ["現價", price(currentPrice)],
    ["成交量", formatVolume(snapshot.volume)],
    ["均線 MA5/20/60", formatMovingAverages(indicators, snapshotSymbol)],
    ["20 日最高/最低", pricePair(indicators.high_20d, indicators.low_20d)],
    ["60 日最高/最低", pricePair(indicators.high_60d, indicators.low_60d, "資料不足")],
    ["布林通道位階", getTechnicalIndicatorLabel("bollinger_position", indicators.bollinger_position)],
    ["MACD 方向", getTechnicalIndicatorLabel("macd_bias", indicators.macd_bias)],
    ["KD 交叉", getTechnicalIndicatorLabel("kd_signal", indicators.kd_signal)],
    ["KD 區間", getTechnicalIndicatorLabel("kd_zone", indicators.kd_zone)],
    ["ADX 趨勢強度", getTechnicalIndicatorLabel("adx_trend_strength", indicators.adx_trend_strength)],
    ["ADX 趨勢方向", getTechnicalIndicatorLabel("adx_trend_direction", indicators.adx_trend_direction)],
    ["OBV 訊號", getTechnicalIndicatorLabel("obv_signal", indicators.obv_signal)],
    ["OBV 20 日趨勢", getTechnicalIndicatorLabel("obv_trend", indicators.obv_trend_20d)],
    [
      "OBV 中長期趨勢",
      `${getTechnicalIndicatorLabel("obv_trend", indicators.obv_trend_mid_long, "資料不足")}${indicators.obv_trend_mid_long_window ? `（${indicators.obv_trend_mid_long_window}）` : ""}`,
    ],
    ["ATR 波動", getTechnicalIndicatorLabel("volatility_level", indicators.volatility_level)],
    ["MFI 資金流量訊號", getTechnicalIndicatorLabel("mfi_signal", indicators.mfi_signal)],
    ["唐奇安通道位階", getTechnicalIndicatorLabel("donchian_position", indicators.donchian_position)],
    ["布林上軌", formatIndicatorNumber(indicators.bollinger_upper, 2)],
    ["布林中軌", formatIndicatorNumber(indicators.bollinger_mid, 2)],
    ["布林下軌", formatIndicatorNumber(indicators.bollinger_lower, 2)],
    ["MACD 線", formatIndicatorNumber(indicators.macd_line, 3)],
    ["MACD 訊號線", formatIndicatorNumber(indicators.macd_signal, 3)],
    ["MACD 動能柱狀體", formatIndicatorNumber(indicators.macd_hist, 3)],
    ["KD K/D", indicatorPair(indicators.kd_k, 1, indicators.kd_d)],
    ["ADX", formatIndicatorNumber(indicators.adx, 1)],
    ["OBV 累積值參考", formatVolume(indicators.obv)],
    ["ATR / ATR%", indicatorPair(indicators.atr, 2, indicators.atr_pct, 2, "%")],
    ["MFI", formatIndicatorNumber(indicators.mfi, 1)],
    ["唐奇安通道上/下緣", indicatorPair(indicators.donchian_upper, 2, indicators.donchian_lower)],
    ...buildPhase1AvwapCopyRows(result.phase1_observation, snapshotSymbol, currentPrice),
    ...buildChipStabilityCopyRows(result.chip_stability_context),
  ];

  return ["技術指標摘要", ...rows.map(([label, value]) => `${label}：${value}`)].join("\n");
}

export async function writeClipboardText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();

  try {
    const copied = document.execCommand("copy");
    if (!copied) throw new Error("Copy command failed");
  } finally {
    document.body.removeChild(textarea);
  }
}
