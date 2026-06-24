import type { ReactNode } from "react";
import type { AnalyzeResponse, TechnicalIndicators, TechnicalProfile, TechnicalProfileSignal } from "../lib/analysisTypes";
import { formatPrice, formatVolume } from "../lib/formatters";
import {
  formatIndicatorNumber,
  formatMovingAverages,
  getTechnicalIndicatorLabel,
} from "../lib/technicalIndicators";

const PRIMARY_LABELS: Record<string, string> = {
  ma_structure: "均線結構",
  support_resistance: "支撐壓力",
  volume_ratio: "量能參與",
  atr_risk: "支撐距離",
  macd_momentum: "MACD 動能",
  obv_trend: "OBV 趨勢",
};

const RISK_LABELS: Record<string, string> = {
  rsi_state: "RSI 過熱",
  bias_state: "BIAS 乖離",
  bollinger_state: "布林過熱",
  atr_state: "ATR 波動",
};

const SECONDARY_LABELS: Record<string, string> = {
  adx: "ADX",
  donchian: "唐奇安",
  mfi: "MFI",
  kd: "KD",
};

const SIGNAL_STATE_LABELS: Record<string, string> = {
  bearish: "偏空",
  bearish_cross: "死亡交叉",
  bearish_flow: "資金偏弱",
  bearish_momentum: "空方動能",
  bullish: "偏多",
  bullish_cross: "黃金交叉",
  bullish_flow: "資金偏多",
  bullish_momentum: "多方動能",
  contained: "風險可控",
  constructive: "結構正向",
  extended: "乖離偏大",
  high: "高波動",
  high_bearish_cross: "高檔死亡交叉",
  low_bullish_cross: "低檔黃金交叉",
  missing: "資料不足",
  neutral: "中性",
  not_extended: "未明顯乖離",
  not_overheated: "未過熱",
  overheated: "過熱",
  positive_histogram: "動能柱偏多",
  range_mid: "區間中段",
  weakening: "結構轉弱",
  wide_stop_distance: "停損距離偏寬",
};

function numberFromSnapshot(snapshot: Record<string, unknown>, key: string): number | null {
  const value = snapshot[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function snapshotSymbol(snapshot: Record<string, unknown>): string | undefined {
  return typeof snapshot.symbol === "string" ? snapshot.symbol : undefined;
}

function impactClass(impact: number): string {
  if (impact > 0) return "text-emerald-600 dark:text-emerald-300";
  if (impact < 0) return "text-red-600 dark:text-red-300";
  return "text-text-muted";
}

function impactLabel(impact: number): string {
  if (impact > 0) return `+${impact}`;
  return `${impact}`;
}

function formatSignalState(state: string): string {
  return SIGNAL_STATE_LABELS[state] ?? state.split("_").join(" ");
}

function signalRows(signals: Record<string, TechnicalProfileSignal>, labels: Record<string, string>) {
  return Object.entries(signals).map(([key, signal]) => ({
    key,
    label: labels[key] ?? key,
    signal,
  }));
}

function TechnicalLayerSection({
  title,
  signals,
  labels,
}: {
  title: string;
  signals: Record<string, TechnicalProfileSignal>;
  labels: Record<string, string>;
}) {
  const rows = signalRows(signals, labels);
  if (rows.length === 0) return null;

  return (
    <section className="border-t border-border-subtle pt-4">
      <h4 className="mb-3 text-xs font-semibold text-text-muted">{title}</h4>
      <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(({ key, label, signal }) => (
          <div key={key} className="min-w-0">
            <div className="mb-1 flex items-center justify-between gap-2">
              <p className="text-xs text-text-muted">{label}</p>
              <span className={`font-mono text-xs font-semibold ${impactClass(signal.impact)}`}>
                {impactLabel(signal.impact)}
              </span>
            </div>
            <p className="text-sm font-medium text-text-primary">{formatSignalState(signal.state)}</p>
            <p className="mt-1 text-xs leading-relaxed text-text-faint">{signal.reason}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function profileCaveats(profile: TechnicalProfile, responseIsFinal: boolean | undefined): string[] {
  const dataQuality = profile.data_quality;
  const caveats = new Set<string>();
  if (responseIsFinal === false || dataQuality.is_final === false) {
    caveats.add("目前是盤中資料，分層摘要不是完整收盤判斷。");
  }
  if ((dataQuality.missing_fields ?? []).length > 0) {
    caveats.add(`資料不足：${(dataQuality.missing_fields ?? []).slice(0, 4).join("、")}`);
  }
  if (dataQuality.ohlcv_aligned === false) {
    caveats.add("OHLC high/low 不完整，支撐壓力不作主要計分。");
  }
  if (dataQuality.volume_aligned === false) {
    caveats.add("成交量序列不完整，量能與 OBV 相關判斷需保守。");
  }
  for (const caveat of profile.caveats ?? []) caveats.add(caveat);
  return Array.from(caveats);
}

function ProfileSummary({
  profile,
  responseIsFinal,
}: {
  profile: TechnicalProfile;
  responseIsFinal: boolean | undefined;
}) {
  const summary = profile.score_summary;
  const caveats = profileCaveats(profile, responseIsFinal);
  return (
    <section className="space-y-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <div>
          <p className="mb-1 text-xs text-text-muted">技術分</p>
          <p className="font-mono text-lg font-semibold text-text-primary">{summary.technical_score}</p>
        </div>
        <div>
          <p className="mb-1 text-xs text-text-muted">主要</p>
          <p className={`font-mono text-sm font-semibold ${impactClass(summary.primary_score)}`}>
            {impactLabel(summary.primary_score)}
          </p>
        </div>
        <div>
          <p className="mb-1 text-xs text-text-muted">風險</p>
          <p className={`font-mono text-sm font-semibold ${impactClass(summary.risk_filter_score)}`}>
            {impactLabel(summary.risk_filter_score)}
          </p>
        </div>
        <div>
          <p className="mb-1 text-xs text-text-muted">輔助</p>
          <p className={`font-mono text-sm font-semibold ${impactClass(summary.secondary_score)}`}>
            {impactLabel(summary.secondary_score)}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded-md border border-border-subtle px-2 py-1 text-text-muted">
          {profile.version}
        </span>
        {profile.data_quality.data_date && (
          <span className="rounded-md border border-border-subtle px-2 py-1 text-text-muted">
            資料日 {profile.data_quality.data_date}
          </span>
        )}
        {profile.data_quality.lookback_days_available != null && (
          <span className="rounded-md border border-border-subtle px-2 py-1 text-text-muted">
            回看 {profile.data_quality.lookback_days_available}/{profile.data_quality.required_lookback_days ?? 60}
          </span>
        )}
      </div>
      {caveats.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
          {caveats.slice(0, 3).join("；")}
        </div>
      )}
    </section>
  );
}

function rawIndicatorRows(
  indicators: TechnicalIndicators,
  snapshot: Record<string, unknown>,
): Array<[string, string]> {
  const symbol = snapshotSymbol(snapshot);
  const price = (value: number | null | undefined) => formatPrice(value, symbol);
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

  return [
    ["現價", price(numberFromSnapshot(snapshot, "current_price"))],
    ["成交量", formatVolume(snapshot.volume)],
    ["均線 MA5/20/60", formatMovingAverages(indicators, symbol)],
    ["20 日最高/最低", pricePair(indicators.high_20d, indicators.low_20d)],
    ["60 日最高/最低", pricePair(indicators.high_60d, indicators.low_60d, "資料不足")],
    ["布林通道", getTechnicalIndicatorLabel("bollinger_position", indicators.bollinger_position)],
    ["MACD 方向", getTechnicalIndicatorLabel("macd_bias", indicators.macd_bias)],
    [
      "KD",
      `${getTechnicalIndicatorLabel("kd_zone", indicators.kd_zone)} / ${getTechnicalIndicatorLabel("kd_signal", indicators.kd_signal)}（K/D ${formatIndicatorNumber(indicators.kd_k, 1)} / ${formatIndicatorNumber(indicators.kd_d, 1)}）`,
    ],
    [
      "ADX",
      `${getTechnicalIndicatorLabel("adx_trend_strength", indicators.adx_trend_strength)} / ${getTechnicalIndicatorLabel("adx_trend_direction", indicators.adx_trend_direction)}（${formatIndicatorNumber(indicators.adx, 1)}）`,
    ],
    [
      "OBV",
      `${getTechnicalIndicatorLabel("obv_signal", indicators.obv_signal)} / ${getTechnicalIndicatorLabel("obv_trend", indicators.obv_trend_20d)}`,
    ],
    ["OBV 中長期", `${getTechnicalIndicatorLabel("obv_trend", indicators.obv_trend_mid_long, "資料不足")}${indicators.obv_trend_mid_long_window ? `（${indicators.obv_trend_mid_long_window}）` : ""}`],
    ["ATR / ATR%", indicatorPair(indicators.atr, 2, indicators.atr_pct, 2, "%")],
    ["MFI", `${formatIndicatorNumber(indicators.mfi, 1)} / ${getTechnicalIndicatorLabel("mfi_signal", indicators.mfi_signal)}`],
    [
      "唐奇安通道",
      `${getTechnicalIndicatorLabel("donchian_position", indicators.donchian_position)}（${formatIndicatorNumber(indicators.donchian_upper, 2)} / ${formatIndicatorNumber(indicators.donchian_lower, 2)}）`,
    ],
    ["布林上/中/下軌", `${formatIndicatorNumber(indicators.bollinger_upper, 2)} / ${formatIndicatorNumber(indicators.bollinger_mid, 2)} / ${formatIndicatorNumber(indicators.bollinger_lower, 2)}`],
    ["MACD 線/訊號/柱", `${formatIndicatorNumber(indicators.macd_line, 3)} / ${formatIndicatorNumber(indicators.macd_signal, 3)} / ${formatIndicatorNumber(indicators.macd_hist, 3)}`],
  ];
}

function RawIndicatorsGrid({
  indicators,
  snapshot,
  title = "完整指標值",
}: {
  indicators: TechnicalIndicators;
  snapshot: Record<string, unknown>;
  title?: string;
}) {
  const rows = rawIndicatorRows(indicators, snapshot);
  return (
    <section className="border-t border-border-subtle pt-4">
      <h4 className="mb-3 text-xs font-semibold text-text-muted">{title}</h4>
      <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <p className="mb-1 text-xs text-text-muted">{label}</p>
            <p className="break-words text-sm font-medium text-text-primary">{value}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function TechnicalIndicatorsPanel({
  result,
  snapshot,
  actions,
  compact = false,
  className = "rounded-xl border border-border bg-card p-4 shadow-sm",
}: {
  result: AnalyzeResponse;
  snapshot: Record<string, unknown>;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  const indicators = result.technical_indicators ?? null;
  const profile = result.technical_profile ?? null;
  const sessionLabel = result.is_final === false ? "盤中資料" : "收盤資料";

  return (
    <article className={className}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold text-text-muted">
            {profile ? "技術指標分層摘要" : "技術指標摘要"}
          </h3>
          <p className="mt-1 text-xs text-text-faint">
            {profile ? `${sessionLabel} · ${profile.version}` : sessionLabel}
          </p>
        </div>
        {actions}
      </div>

      {!indicators ? (
        <div className="rounded-md border border-border-subtle px-3 py-2 text-sm text-text-muted">
          技術指標資料不足，請稍後更新。
        </div>
      ) : (
        <div className={compact ? "space-y-4" : "space-y-5"}>
          {profile && (
            <>
              <ProfileSummary profile={profile} responseIsFinal={result.is_final} />
              <TechnicalLayerSection title="主要判斷" signals={profile.primary_score_inputs} labels={PRIMARY_LABELS} />
              <TechnicalLayerSection title="風險與過熱濾網" signals={profile.risk_overheat_filters} labels={RISK_LABELS} />
              <TechnicalLayerSection title="輔助證據" signals={profile.secondary_evidence} labels={SECONDARY_LABELS} />
            </>
          )}
          <RawIndicatorsGrid
            indicators={indicators}
            snapshot={snapshot}
            title={profile ? "完整指標值" : "技術指標值"}
          />
        </div>
      )}
    </article>
  );
}
