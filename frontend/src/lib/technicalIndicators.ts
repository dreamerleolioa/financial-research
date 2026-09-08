import type { AnalyzeResponse, ChipStabilityContext, Phase1Observation, TechnicalIndicators } from "./analysisTypes";
import { formatPrice, formatVolume } from "./formatters";
import { formatDataMissingReason } from "./presentationLabels";

export type CopyStatus = "idle" | "success" | "error";

export type TechnicalIndicatorsCopyPayload = Pick<
  AnalyzeResponse,
  "symbol_name" | "technical_indicators" | "is_final" | "phase1_observation" | "chip_stability_context"
>;

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

const MACD_HIST_TREND_LABEL: Record<string, { label: string }> = {
  accelerating_bullish: { label: "多方動能擴張" },
  bullish_fading: { label: "多方動能收斂" },
  accelerating_bearish: { label: "空方動能擴張" },
  bearish_recovering: { label: "空方動能收斂" },
  flat: { label: "動能持平" },
  missing: { label: "資料不足" },
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
  macd_hist_trend: MACD_HIST_TREND_LABEL,
  mfi_signal: MFI_SIGNAL_LABEL,
  donchian_position: DONCHIAN_POSITION_LABEL,
} as const;

export type TechnicalLabelKind = keyof typeof TECHNICAL_LABELS;

export function formatIndicatorNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function formatSignedPercent(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "資料不足";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function buildIndicatorComparisonRows(indicators: TechnicalIndicators): Array<[string, string]> {
  const signed = (value: number | null | undefined, digits: number) =>
    value == null || !Number.isFinite(value)
      ? "資料不足"
      : `${value > 0 ? "+" : ""}${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  return [
    ["指標資料日／前一交易日", `${indicators.indicator_data_date ?? "資料不足"} / ${indicators.indicator_previous_date ?? "資料不足"}`],
    ["MACD 前一交易日柱體（同序列）", formatIndicatorNumber(indicators.macd_hist_previous, 3)],
    ["MACD 柱體單日增減", signed(indicators.macd_hist_change_1d, 3)],
    ["MACD 三日分類資料日", indicators.macd_trend_data_date ?? "資料不足"],
    ["MACD 三交易日前資料日", indicators.macd_trend_previous_date ?? "資料不足"],
    ["MACD 三日比較末柱（完整日K）", formatIndicatorNumber(indicators.macd_trend_hist, 3)],
    ["MACD 三交易日前柱體（同序列）", formatIndicatorNumber(indicators.macd_hist_3d_previous, 3)],
    ["MACD 三日淨變化", signed(indicators.macd_hist_change_3d, 3)],
    ["MACD 三日正規化分母（比較末日收盤價）", formatIndicatorNumber(indicators.macd_trend_price, 2)],
    ["MACD 比較說明", `三日分類不代表每天同向變化；${indicators.input_context?.indicator_mode === "completed_daily" && indicators.input_context.indicator_close_confirmed === true
      ? `MACD 僅使用截至 ${indicators.indicator_data_date ?? "未確認日期"} 的完整日K，${indicators.macd_hist_change_1d != null ? `單日增減 ${signed(indicators.macd_hist_change_1d, 3)} 已收盤確認。` : "單日增減資料不足。"}`
      : indicators.input_context?.indicator_mode === "intraday_estimate"
        ? "單日增減為盤中暫估，尚未收盤確認；三日分類另使用完整日K。"
        : "單日增減收盤狀態未確認。"} 另外取得的即時行情未納入 MACD 重算。`],
    ["OBV 起算日（首筆歸零）", indicators.obv_start_date ?? "資料不足"],
    ["OBV 前一交易日累積值（同序列）", signed(indicators.obv_previous, 0)],
    ["OBV 單日增減（同序列）", signed(indicators.obv_change_1d, 0)],
    ["OBV 訊號比較起日", indicators.obv_window_previous_date ?? "資料不足"],
    ["OBV 比較起日收盤價", formatIndicatorNumber(indicators.obv_window_previous_close, 2)],
    ["OBV 比較末日收盤／盤中價", formatIndicatorNumber(indicators.obv_window_close, 2)],
    ["OBV 比較窗價格淨變化", formatSignedPercent(indicators.obv_window_price_change_pct)],
    ["OBV 比較起日累積值（同序列）", signed(indicators.obv_window_previous, 0)],
    ["OBV 比較窗淨變化", signed(indicators.obv_window_change, 0)],
    ["OBV 比較說明", "累積值隨歷史起點與重算而變，請使用同序列增減，勿跨摘要相減。"],
  ];
}

export function formatPercentile(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "資料不足";
  return `${value.toFixed(1)} 百分位`;
}

export function getTechnicalIndicatorLabel(
  kind: TechnicalLabelKind,
  value: string | null | undefined,
  emptyLabel = "—",
): string {
  if (!value) return emptyLabel;
  return TECHNICAL_LABELS[kind][value]?.label ?? "其他狀態";
}

export function getAnalyzeSymbolName(
  result: Pick<AnalyzeResponse, "symbol_name"> | null,
  snapshot: Record<string, unknown>,
): string | null {
  if (typeof result?.symbol_name === "string" && result.symbol_name.trim()) return result.symbol_name.trim();
  if (typeof snapshot.name === "string" && snapshot.name.trim()) return snapshot.name.trim();
  return null;
}

export function formatMovingAverages(indicators: TechnicalIndicators, _snapshotSymbol?: string): string {
  return indicators.ma5 != null || indicators.ma20 != null || indicators.ma60 != null
    ? `${formatIndicatorNumber(indicators.ma5, 2)} / ${formatIndicatorNumber(indicators.ma20, 2)} / ${formatIndicatorNumber(indicators.ma60, 2)}`
    : "—";
}

export function formatDailyOhlc(snapshot: Record<string, unknown>, snapshotSymbol?: string): string {
  const prefix = snapshot.market_current_price_source === "twse_mis" ? "market_" : "";
  const prices = ["day_open", "day_high", "day_low"].map((field) => {
    const key = prefix + field;
    const value = snapshot[key];
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
  });

  return prices.some((value) => value != null)
    ? prices.map((value) => formatPrice(value, snapshotSymbol)).join(" / ")
    : "—";
}

export function formatAverageVolumes(indicators: TechnicalIndicators): string {
  const volumes = [indicators.avg_volume_20, indicators.avg_volume_60];
  const hasVolume = volumes.some((value) => typeof value === "number" && Number.isFinite(value));
  return hasVolume ? volumes.map((value) => formatVolume(value)).join(" / ") : "資料不足";
}

export function getPriceLimitLabel(snapshot: Record<string, unknown>): "漲停" | "跌停" | null {
  if (snapshot.price_limit_status === "limit_up") return "漲停";
  if (snapshot.price_limit_status === "limit_down") return "跌停";
  return null;
}

export function getMarketCurrentPrice(snapshot: Record<string, unknown>): number | null {
  const marketPrice = snapshot.market_current_price;
  if (typeof marketPrice === "number" && Number.isFinite(marketPrice) && marketPrice > 0) {
    return marketPrice;
  }
  const snapshotPrice = snapshot.current_price;
  return typeof snapshotPrice === "number" && Number.isFinite(snapshotPrice) && snapshotPrice > 0
    ? snapshotPrice
    : null;
}

function formatPhase1Distance(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatPhase1MissingReason(reason: string | null | undefined): string {
  return formatDataMissingReason(reason, "AVWAP 資料不足");
}

function buildPhase1AvwapCopyRows(
  observation: Phase1Observation | null | undefined,
  referencePrice?: number | null,
): Array<[string, string]> {
  if (!observation) return [];

  const entries = Object.entries(observation.anchors ?? {}).filter(([, anchor]) => anchor.available !== false);
  const priority: Map<string, number> = new Map(PHASE1_ANCHOR_ORDER.map((key, index) => [key, index]));
  const anchorRows: Array<[string, string]> = entries
    .sort(([left], [right]) => (priority.get(left) ?? 99) - (priority.get(right) ?? 99) || left.localeCompare(right))
    .map(([key, anchor]) => {
      const distance = referencePrice != null && anchor.avwap != null && anchor.avwap > 0
        ? (referencePrice - anchor.avwap) / anchor.avwap * 100
        : null;
      const parts = [formatIndicatorNumber(anchor.avwap, 2), `距離 ${formatPhase1Distance(distance)}`];
      if (anchor.anchor_date) parts.push(`錨點日 ${anchor.anchor_date}`);
      if (anchor.estimated) parts.push("日資料估算");
      return [PHASE1_ANCHOR_LABEL[key] ?? "其他 AVWAP 觀察線", parts.join(" / ")];
    });

  if (anchorRows.length > 0) {
    return [["AVWAP 資料日", observation.data_date], ...anchorRows];
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

function buildChipStabilityCopyRows(context: ChipStabilityContext | null | undefined): Array<[string, string]> {
  if (!context) return [];
  return [
    [
      "千張大戶持股比例",
      `${formatRatioPct(context.thousand_lot_holder_ratio)}${context.as_of_date ? `（${context.as_of_date}）` : ""}`,
    ],
    ["較上週變化", formatSignedDelta(context.thousand_lot_holder_ratio_delta_pp)],
    ...buildChipStabilityHistoryRows(context),
  ];
}

export function buildTechnicalIndicatorsCopyText(
  result: TechnicalIndicatorsCopyPayload,
  snapshot: Record<string, unknown>,
): string {
  const indicators = result.technical_indicators;
  const snapshotSymbol = typeof snapshot.symbol === "string" ? snapshot.symbol : undefined;
  const displaySymbol = snapshotSymbol ?? "—";
  const symbolName = getAnalyzeSymbolName(result, snapshot);
  const marketSessionLabel = result.is_final === false ? "盤中快照" : result.is_final === true ? "收盤快照" : "未確認";
  const currentPrice = getMarketCurrentPrice(snapshot);
  const currentPriceSource = snapshot.market_current_price_source === "twse_mis" ? "（TWSE MIS 即時）" : "";
  const priceLimitLabel = getPriceLimitLabel(snapshot);
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
      `行情狀態：${marketSessionLabel}`,
      "技術指標：資料不足",
      ...buildPhase1AvwapCopyRows(result.phase1_observation, currentPrice).map(
        ([label, value]) => `${label}：${value}`,
      ),
      ...buildChipStabilityCopyRows(result.chip_stability_context).map(([label, value]) => `${label}：${value}`),
    ].join("\n");
  }

  const rows: Array<[string, string]> = [
    ["股票名稱", symbolName ?? "—"],
    ["股票代碼", displaySymbol],
    ["行情狀態", marketSessionLabel],
    ["現價", `${price(currentPrice)}${currentPriceSource}${priceLimitLabel ? `（${priceLimitLabel}）` : ""}`],
    ["行情開／高／低", formatDailyOhlc(snapshot, snapshotSymbol)],
    ...buildIndicatorSourceRows(indicators, snapshot),
    ["成交量", formatVolume(snapshot.volume)],
    ["20／60 日均成交量", formatAverageVolumes(indicators)],
    ["均線 MA5/20/60", formatMovingAverages(indicators, snapshotSymbol)],
    ["近20根指標日K最高/最低（含末根）", pricePair(indicators.high_20d, indicators.low_20d)],
    ["前20個完整交易日最高/最低（突破基準）", pricePair(indicators.prior_high_20d, indicators.prior_low_20d)],
    ["60 日最高/最低", pricePair(indicators.high_60d, indicators.low_60d, "資料不足")],
    ["布林通道位階", formatBollingerState(indicators, currentPrice)],
    ["MACD 方向", getTechnicalIndicatorLabel("macd_bias", indicators.macd_bias)],
    ["MA20 5日斜率", formatSignedPercent(indicators.ma20_slope_pct_5d, 3)],
    ["MA60 10日斜率", formatSignedPercent(indicators.ma60_slope_pct_10d, 3)],
    ["MACD 柱體 3日淨變化／股價", formatSignedPercent(indicators.macd_hist_slope_pct_3d, 4)],
    ["MACD 動能變化（3日）", getTechnicalIndicatorLabel("macd_hist_trend", indicators.macd_hist_trend)],
    ["ATR% 60日分位", formatPercentile(indicators.atr_pct_percentile_60d)],
    ["布林帶寬 60日分位", formatPercentile(indicators.bollinger_bandwidth_percentile_60d)],
    ["KD 本次交叉", formatKdEvent(indicators)],
    ["KD 區間", getTechnicalIndicatorLabel("kd_zone", indicators.kd_zone)],
    ["ADX 趨勢強度", getTechnicalIndicatorLabel("adx_trend_strength", indicators.adx_trend_strength)],
    ["DMI 方向", getTechnicalIndicatorLabel("adx_trend_direction", indicators.adx_trend_direction)],
    ["OBV 訊號", getTechnicalIndicatorLabel("obv_signal", indicators.obv_signal)],
    ["OBV 20 日趨勢", getTechnicalIndicatorLabel("obv_trend", indicators.obv_trend_20d)],
    [
      "OBV 中長期趨勢",
      `${getTechnicalIndicatorLabel("obv_trend", indicators.obv_trend_mid_long, "資料不足")}${indicators.obv_trend_mid_long_window ? `（${indicators.obv_trend_mid_long_window}）` : ""}`,
    ],
    ["ATR 波動", getTechnicalIndicatorLabel("volatility_level", indicators.volatility_level)],
    ["MFI 資金流量訊號", getTechnicalIndicatorLabel("mfi_signal", indicators.mfi_signal)],
    ["唐奇安通道位階", formatDonchianState(indicators, currentPrice)],
    ["布林上軌", formatIndicatorNumber(indicators.bollinger_upper, 2)],
    ["布林中軌", formatIndicatorNumber(indicators.bollinger_mid, 2)],
    ["布林下軌", formatIndicatorNumber(indicators.bollinger_lower, 2)],
    ["MACD 線", formatIndicatorNumber(indicators.macd_line, 3)],
    ["MACD 訊號線", formatIndicatorNumber(indicators.macd_signal, 3)],
    ["MACD 動能柱狀體", formatIndicatorNumber(indicators.macd_hist, 3)],
    ["MACD 柱體／原始快照價", formatSignedPercent(indicators.macd_hist_pct, 4)],
    ["KD K/D", indicatorPair(indicators.kd_k, 1, indicators.kd_d)],
    ["ADX", formatIndicatorNumber(indicators.adx, 1)],
    ["OBV 累積值參考", formatVolume(indicators.obv)],
    ...buildIndicatorComparisonRows(indicators),
    ["ATR / ATR%", indicatorPair(indicators.atr, 2, indicators.atr_pct, 2, "%")],
    ["MFI", formatIndicatorNumber(indicators.mfi, 1)],
    ["唐奇安通道上/下緣", indicatorPair(indicators.donchian_upper, 2, indicators.donchian_lower)],
    ...buildPhase1AvwapCopyRows(result.phase1_observation, currentPrice),
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

export function formatKdEvent(indicators: TechnicalIndicators): string {
  if (indicators.kd_signal === "neutral") return "無新交叉";
  const label = getTechnicalIndicatorLabel("kd_signal", indicators.kd_signal, "無法更新");
  return indicators.input_context?.indicator_mode === "intraday_estimate" ? `盤中${label}（尚未收盤確認）` : label;
}

export function formatDonchianState(indicators: TechnicalIndicators, displayPrice?: number | null): string {
  const upper = indicators.donchian_upper;
  const lower = indicators.donchian_lower;
  if (displayPrice == null || upper == null || lower == null) return "資料不足，無法比較";
  if (displayPrice > upper) return "高於唐奇安上緣";
  if (displayPrice < lower) return "低於唐奇安下緣";
  if (displayPrice === upper) return "觸及唐奇安上緣，尚未突破";
  if (displayPrice === lower) return "觸及唐奇安下緣，尚未跌破";
  return "現價位於唐奇安通道內";
}

function donchianEvent(indicators: TechnicalIndicators, price: number | null, quoteDate: string | null): string {
  const context = indicators.input_context;
  if (!quoteDate) return "無法確認（行情交易日未知）；僅可計算價格位置";
  if (!context?.breakout_baseline_through || quoteDate <= context.breakout_baseline_through) return "無法確認（行情未晚於突破基準日期）";
  if (price == null || indicators.donchian_upper == null || indicators.donchian_lower == null) return "無法確認（價格或通道資料不足）";
  if (price <= indicators.donchian_upper && price >= indicators.donchian_lower) return "快照價未越界，未形成突破";
  if (context.breakout_close_confirmed === true && quoteDate === indicators.indicator_data_date && price === context.indicator_close) return "收盤價已越界；新交叉事件仍需前次價格確認";
  return "快照價已越界，尚未收盤確認；新突破事件仍需前次價格確認";
}

export function formatBollingerState(indicators: TechnicalIndicators, displayPrice?: number | null): string {
  const close = displayPrice ?? indicators.input_context?.breakout_reference_price;
  const upper = indicators.bollinger_upper;
  const lower = indicators.bollinger_lower;
  if (close != null && upper != null && lower != null) {
    if (close > upper) return "高於上軌";
    if (close < lower) return "低於下軌";
    if (upper <= lower) return "區間平坦";
    if (close >= upper * 0.99) return "接近上軌";
    if (close <= lower * 1.01) return "接近下軌";
    return close >= (upper + lower) / 2 ? "中軌上方" : "中軌下方";
  }
  if (close != null && upper != null && close > upper) return "高於上軌";
  if (close != null && lower != null && close < lower) return "低於下軌";
  return getTechnicalIndicatorLabel("bollinger_position", indicators.bollinger_position);
}

function taipeiTimestamp(value: unknown): string | null {
  if (typeof value !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return `${new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(date)} +08:00`;
}

export function buildIndicatorSourceRows(
  indicators: TechnicalIndicators,
  snapshot: Record<string, unknown>,
): Array<[string, string]> {
  const context = indicators.input_context;
  const isMis = snapshot.market_current_price_source === "twse_mis";
  const quoteTime = taipeiTimestamp(isMis ? snapshot.market_quote_time : snapshot.quote_time);
  const fetchedTime = taipeiTimestamp(snapshot.fetched_at);
  const quoteDate = quoteTime?.slice(0, 10)
    ?? (isMis && typeof snapshot.market_trade_date === "string" ? snapshot.market_trade_date : null);
  const mode = context?.indicator_mode;
  const volume = typeof snapshot.volume === "number" ? snapshot.volume : null;
  const average = indicators.avg_volume_20;
  const kdOrder = indicators.kd_k != null && indicators.kd_d != null
    ? indicators.kd_k > indicators.kd_d ? "K > D" : indicators.kd_k < indicators.kd_d ? "K < D" : "K = D"
    : "資料不足";
  return [
    ["行情交易日", quoteDate ?? "未提供"],
    ["行情／指標日期核對", quoteDate && indicators.indicator_data_date
      ? quoteDate === indicators.indicator_data_date ? "同一交易日" : "不同交易日；日線指標未以所列現價重算"
      : "資料不足，無法核對"],
    ["行情時間", quoteTime ?? "未提供"],
    ["原始快照擷取時間（非成交時間）", fetchedTime ?? "未提供"],
    ["歷史完整日K截至", context?.history_completed_through ?? "未確認"],
    ["指標所屬交易日", indicators.indicator_data_date ?? "未確認"],
    ["指標狀態", mode === "completed_daily" ? "完整日線收盤" : mode === "intraday_estimate" ? "日線盤中暫估" : "未確認"],
    ["即時價是否納入指標重算", "否（另外取得的行情價未併入原始日K）"],
    ["指標模式", mode === "intraday_estimate" ? "當日日線盤中暫估" : mode === "completed_daily" ? "僅使用已完成日線" : "未確認"],
    ["指標日K收盤確認", context?.indicator_close_confirmed === true ? "是" : context?.indicator_close_confirmed === false ? "否" : "未確認"],
    ["原始快照參考價", formatIndicatorNumber(context?.breakout_reference_price, 2)],
    ["指標末根日K收盤／盤中價", formatIndicatorNumber(context?.indicator_close, 2)],
    ["指標輸入", "使用原始日K序列；未把另外取得的即時現價塞入序列重算。"],
    ["日線高低收輸入", context?.hlc_status === "complete" ? "齊全（以指標資料日為準）" : context?.hlc_status === "unavailable" ? "不完整，依賴高低價的指標無法更新" : "未提供，無法確認"],
    ["突破事件", donchianEvent(indicators, getMarketCurrentPrice(snapshot), quoteDate)],
    ["突破比較說明", "以所列現價比較完整日K基準；即時越界不代表收盤突破。"],
    ["突破基準截至", context?.breakout_baseline_through ?? "未確認"],
    ["KD 排列", kdOrder],
    ["前一交易日 K／D", `${formatIndicatorNumber(indicators.kd_previous_k, 2)} / ${formatIndicatorNumber(indicators.kd_previous_d, 2)}`],
    ["＋DI／−DI", `${formatIndicatorNumber(indicators.dmi_plus, 2)} / ${formatIndicatorNumber(indicators.dmi_minus, 2)}`],
    ["OBV 訊號判定期間", context?.obv_lookback != null ? `比較末根與 ${context.obv_lookback} 個交易日前的價格與 OBV 淨變化，非獨立資金流入證據。` : "未提供"],
    ["成交量單位／來源", `股／${context?.volume_source === "history_fallback" ? "沿用歷史日線量" : context?.volume_source === "realtime" ? "原始行情快照" : "來源未確認"}`],
    ["成交量資料日／狀態", `${context?.volume_data_date ?? "未提供"} / ${context?.volume_state === "full_day" ? "全日" : context?.volume_state === "intraday_cumulative" ? "盤中累計" : "未確認"}`],
    ["20日均量口徑", context && mode !== "unknown" ? `完整日均量；${context.volume_average_excludes_signal_bar ? "排除當日未完成日K" : "包含最新完整日K"}` : "未確認"],
    ["快照成交量／20日完整日均量", volume != null && average != null && average > 0 ? `${(volume / average).toFixed(2)} 倍（不是同時段量比）` : "資料不足"],
    ["日線來源／價格還原", `${context?.history_source ?? "未提供"} / ${context?.price_adjustment ?? "未提供"}`],
    ["MA20 5日斜率公式", "(MA20[t] / MA20[t-5] - 1) × 100%；t 為完整日K末日"],
    ["MA60 10日斜率公式", "(MA60[t] / MA60[t-10] - 1) × 100%；t 為完整日K末日"],
    ["指標設定版本", context?.formula_version ?? "未提供"],
    ["一致性檢查", context?.consistency_issues ? context.consistency_issues.length ? context.consistency_issues.join("；") : "可檢查項目通過；缺資料項目不視為通過" : "未執行"],
  ];
}
