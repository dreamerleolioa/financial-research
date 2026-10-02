import type { Page, Route } from "@playwright/test";

const API_ORIGIN = "http://127.0.0.1:8001";

export const testUser = {
  id: 1,
  email: "e2e@example.com",
  name: "E2E Researcher",
  avatar_url: null,
};

export const quickAnalyzeResult = {
  snapshot: {
    symbol: "3661.TW",
    current_price: 3100,
    market_current_price: 3120,
    market_current_price_source: "twse_mis",
    market_quote_time: "2026-07-16T10:25:30+08:00",
    market_day_open: 3075,
    market_day_high: 3155,
    market_day_low: 3050,
    price_limit_quote_price: 3120,
    price_limit_status: "limit_up",
    limit_up_price: 3120,
    limit_down_price: 2555,
    day_open: 3075,
    day_high: 3155,
    day_low: 3050,
    change_percent: 1.6,
    volume: 2380,
    data_date: "2026-07-16",
  },
  symbol_name: "世芯-KY",
  analysis: "",
  analysis_detail: null,
  cleaned_news: null,
  cleaned_news_quality: null,
  news_display_items: [],
  confidence_score: null,
  cross_validation_note: null,
  strategy_type: null,
  entry_zone: null,
  stop_loss: null,
  holding_period: null,
  action_plan_tag: "neutral",
  technical_indicators: {
    ma5: 3080,
    ma20: 3010,
    ma60: 2860,
    avg_volume_20: 2100,
    avg_volume_60: 1800,
    high_20d: 3180,
    low_20d: 2920,
    high_60d: 3240,
    low_60d: 2650,
    rsi14: 61,
    macd_bias: "bullish",
    atr: 88,
    ma20_slope_pct_5d: 1.234,
    ma60_slope_pct_10d: 2.345,
    indicator_data_date: "2026-07-16",
    indicator_previous_date: "2026-07-15",
    macd_trend_data_date: "2026-07-15",
    macd_hist: 2.293,
    macd_hist_previous: 2.126,
    macd_hist_change_1d: 0.167,
    obv: 597572897,
    obv_previous: 584037123,
    obv_change_1d: 13535774,
    obv_start_date: "2025-07-17",
    macd_hist_slope_pct_3d: -0.0456,
    macd_hist_trend: "bullish_fading",
    atr_pct_percentile_60d: 82.5,
    bollinger_bandwidth_percentile_60d: 77.5,
  },
  technical_profile: null,
  action_plan: {
    action: "wait",
    target_zone: "3010 至 3080",
    defense_line: "跌破 3010 後重新評估",
    momentum_expectation: "量價同步才視為有效突破",
    conviction_level: "medium",
    suggested_position_size: "先觀察，不預設部位",
  },
  risk_state: "observe",
  risk_state_label: "等待確認",
  discipline_triggers: ["跌破 MA20 且量能放大"],
  observation_conditions: ["量縮回測 MA20", "突破前高並維持成交量"],
  risk_control_reference: {
    reference: "MA20 3010",
    reference_type: "ma20",
  },
  command_language_deprecated: {},
  institutional_flow_label: null,
  data_confidence: 88,
  is_final: true,
  intraday_disclaimer: null,
  errors: [],
  fundamental_data: null,
  shared_context: null,
  chip_stability_context: null,
  phase1_observation: null,
};

export const radarRun = {
  run_date: "2026-07-16",
  status: "completed",
  data_dates: {
    ohlcv: "2026-07-16",
    technical_profile: "2026-07-16",
  },
  market_context: {},
  candidates: [
    {
      symbol: "2330.TW",
      name: "台積電",
      primary_bucket: "support_retest",
      secondary_buckets: ["institutional_accumulation"],
      observation_score: 86,
      risk_labels: [],
      repeat_status: "new",
      explanation: "價格維持在關鍵均線附近，仍需確認隔日量價延續。",
      scoring_version: "e2e",
      rule_version: "e2e",
      bucket_scores: { support_retest: 62 },
      score_breakdown: {},
      input_snapshot: {},
      data_dates: { ohlcv: "2026-07-16" },
      matched_rules: [
        {
          rule_id: "support_retest_ma20",
          label: "收盤維持在關鍵均線附近",
          details: { close: 1085, ma20: 1048 },
        },
      ],
      background_context_labels: [],
    },
  ],
};

export const activeEtfDaily = {
  data_date: "2026-08-28",
  available_dates: ["2026-08-28", "2026-08-27"],
  generated_at: "2026-08-31T08:05:00+08:00",
  expected_funds: 5,
  covered_funds: 3,
  summary: {
    changed_funds: 3,
    changed_stocks: 4,
    changed_rows: 5,
    additions: 2,
    increases: 2,
    decreases: 1,
    removals: 0,
  },
  funds: [
    {
      fund_code: "00985A",
      name: "野村臺灣智慧優選主動式ETF",
      category: "國內成分證券ETF",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00985A.TW",
      status: "ready",
      verification_status: "verified",
      source_count: 2,
      verification_reason: "share_inventory_match",
      sources: [
        {
          source_provider: "issuer_official",
          source_url: "https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo=00985A",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T07:58:01+08:00",
          payload_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
        {
          source_provider: "moneydj",
          source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00985A.TW",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T07:58:00+08:00",
          payload_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        },
      ],
      evidence_periods: [
        {
          period: "current",
          data_date: "2026-08-28",
          verification_status: "verified",
          source_count: 2,
          verification_reason: "share_inventory_match",
          sources: [
            {
              source_provider: "issuer_official",
              source_url: "https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo=00985A",
              data_date: "2026-08-28",
              fetched_at: "2026-08-31T07:58:01+08:00",
              payload_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            {
              source_provider: "moneydj",
              source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00985A.TW",
              data_date: "2026-08-28",
              fetched_at: "2026-08-31T07:58:00+08:00",
              payload_hash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            },
          ],
        },
        {
          period: "previous",
          data_date: "2026-08-27",
          verification_status: "single_source",
          source_count: 1,
          verification_reason: "official_source_unsupported",
          sources: [
            {
              source_provider: "moneydj",
              source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00985A.TW",
              data_date: "2026-08-27",
              fetched_at: "2026-08-30T07:58:00+08:00",
              payload_hash: "1212121212121212121212121212121212121212121212121212121212121212",
            },
          ],
        },
      ],
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      latest_data_date: "2026-08-28",
      fetched_at: "2026-08-31T07:58:00+08:00",
      change_count: 2,
      common_scale_ratio: 1.04,
    },
    {
      fund_code: "00980A",
      name: "野村臺灣智慧優選主動式ETF",
      category: "國內成分證券ETF",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00980A.TW",
      status: "ready",
      verification_status: "verified",
      source_count: 2,
      verification_reason: "share_inventory_match",
      sources: [
        {
          source_provider: "issuer_official",
          source_url: "https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo=00980A",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T07:59:01+08:00",
          payload_hash: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        },
        {
          source_provider: "moneydj",
          source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00980A.TW",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T07:59:00+08:00",
          payload_hash: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        },
      ],
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      latest_data_date: "2026-08-28",
      fetched_at: "2026-08-31T07:59:00+08:00",
      change_count: 2,
      common_scale_ratio: 1.01,
    },
    {
      fund_code: "00982A",
      name: "主動群益台灣強棒",
      category: "國內成分證券ETF",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00982A.TW",
      status: "ready",
      verification_status: "single_source",
      source_count: 1,
      verification_reason: "official_source_unsupported",
      sources: [
        {
          source_provider: "moneydj",
          source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00982A.TW",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T08:00:00+08:00",
          payload_hash: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        },
      ],
      evidence_periods: [
        {
          period: "current",
          data_date: "2026-08-28",
          verification_status: "single_source",
          source_count: 1,
          verification_reason: "official_source_unsupported",
          sources: [
            {
              source_provider: "moneydj",
              source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00982A.TW",
              data_date: "2026-08-28",
              fetched_at: "2026-08-31T08:00:00+08:00",
              payload_hash: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            },
          ],
        },
        {
          period: "previous",
          data_date: "2026-08-27",
          verification_status: "single_source",
          source_count: 1,
          verification_reason: "official_source_unsupported",
          sources: [
            {
              source_provider: "moneydj",
              source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00982A.TW",
              data_date: "2026-08-27",
              fetched_at: "2026-08-30T08:00:00+08:00",
              payload_hash: "3434343434343434343434343434343434343434343434343434343434343434",
            },
          ],
        },
      ],
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      latest_data_date: "2026-08-28",
      fetched_at: "2026-08-31T08:00:00+08:00",
      change_count: 1,
      common_scale_ratio: 1.02,
    },
    {
      fund_code: "00983A",
      name: "測試來源衝突基金",
      category: "國內成分證券ETF",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00983A.TW",
      status: "source_conflict",
      verification_status: "conflict",
      source_count: 2,
      verification_reason: "holding_mismatch",
      sources: [
        {
          source_provider: "issuer_official",
          source_url: "https://issuer.example/00983A",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T08:01:01+08:00",
          payload_hash: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        },
        {
          source_provider: "moneydj",
          source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00983A.TW",
          data_date: "2026-08-28",
          fetched_at: "2026-08-31T08:01:00+08:00",
          payload_hash: "1111111111111111111111111111111111111111111111111111111111111111",
        },
      ],
      data_date: "2026-08-28",
      previous_date: null,
      latest_data_date: "2026-08-28",
      fetched_at: "2026-08-31T08:01:00+08:00",
      change_count: 0,
      common_scale_ratio: null,
    },
    {
      fund_code: "00409A",
      name: "測試尚未更新基金",
      category: "國內成分證券ETF",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00409A.TW",
      status: "missing",
      verification_status: null,
      source_count: 0,
      verification_reason: null,
      sources: [],
      data_date: null,
      previous_date: null,
      latest_data_date: "2026-08-27",
      fetched_at: null,
      change_count: 0,
      common_scale_ratio: null,
    },
  ],
  changes: [
    {
      action: "added",
      fund_code: "00985A",
      fund_name: "野村臺灣智慧優選主動式ETF",
      symbol: "2330.TW",
      name: "台積電",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00985A.TW",
      verification_status: "single_source",
      source_count: 1,
      fetched_at: "2026-08-31T07:58:00+08:00",
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      current_shares: 800000,
      previous_shares: 0,
      share_delta: 800000,
      share_delta_pct: null,
      current_weight_pct: "8.25",
      previous_weight_pct: "0",
      weight_delta_pct_points: "8.25",
      relative_share_change_pct: null,
      likely_fund_scale_change: false,
    },
    {
      action: "increased",
      fund_code: "00985A",
      fund_name: "野村臺灣智慧優選主動式ETF",
      symbol: "2454.TW",
      name: "聯發科",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00985A.TW",
      verification_status: "single_source",
      source_count: 1,
      fetched_at: "2026-08-31T07:58:00+08:00",
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      current_shares: 520000,
      previous_shares: 500000,
      share_delta: 20000,
      share_delta_pct: "4",
      current_weight_pct: "6.45",
      previous_weight_pct: "6.32",
      weight_delta_pct_points: "0.13",
      relative_share_change_pct: "0",
      likely_fund_scale_change: true,
    },
    {
      action: "added",
      fund_code: "00980A",
      fund_name: "野村臺灣智慧優選主動式ETF",
      symbol: "2330.TW",
      name: "台積電",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00980A.TW",
      verification_status: "verified",
      source_count: 2,
      fetched_at: "2026-08-31T07:59:00+08:00",
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      current_shares: 320000,
      previous_shares: 0,
      share_delta: 320000,
      share_delta_pct: null,
      current_weight_pct: "4.70",
      previous_weight_pct: "0",
      weight_delta_pct_points: "4.70",
      relative_share_change_pct: null,
      likely_fund_scale_change: false,
    },
    {
      action: "decreased",
      fund_code: "00980A",
      fund_name: "野村臺灣智慧優選主動式ETF",
      symbol: "2317.TW",
      name: "鴻海",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00980A.TW",
      verification_status: "verified",
      source_count: 2,
      fetched_at: "2026-08-31T07:59:00+08:00",
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      current_shares: 460000,
      previous_shares: 500000,
      share_delta: -40000,
      share_delta_pct: "-8",
      current_weight_pct: "3.61",
      previous_weight_pct: "3.95",
      weight_delta_pct_points: "-0.34",
      relative_share_change_pct: "-8.91",
      likely_fund_scale_change: false,
    },
    {
      action: "increased",
      fund_code: "00982A",
      fund_name: "主動群益台灣強棒",
      symbol: "2881.TW",
      name: "富邦金",
      source_provider: "moneydj",
      source_url: "https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=00982A.TW",
      verification_status: "single_source",
      source_count: 1,
      fetched_at: "2026-08-31T08:00:00+08:00",
      data_date: "2026-08-28",
      previous_date: "2026-08-27",
      current_shares: 120000,
      previous_shares: 100000,
      share_delta: 20000,
      share_delta_pct: "20",
      current_weight_pct: "2.40",
      previous_weight_pct: "2.05",
      weight_delta_pct_points: "0.35",
      relative_share_change_pct: "17.65",
      likely_fund_scale_change: false,
    },
  ],
  consensus: [
    {
      symbol: "2330.TW",
      name: "台積電",
      direction: "increase",
      fund_count: 2,
      added_count: 2,
      increased_count: 0,
      decreased_count: 0,
      removed_count: 0,
    },
    {
      symbol: "2454.TW",
      name: "聯發科",
      direction: "increase",
      fund_count: 1,
      added_count: 0,
      increased_count: 1,
      decreased_count: 0,
      removed_count: 0,
    },
    {
      symbol: "2317.TW",
      name: "鴻海",
      direction: "decrease",
      fund_count: 1,
      added_count: 0,
      increased_count: 0,
      decreased_count: 1,
      removed_count: 0,
    },
    {
      symbol: "2881.TW",
      name: "富邦金",
      direction: "increase",
      fund_count: 1,
      added_count: 0,
      increased_count: 1,
      decreased_count: 0,
      removed_count: 0,
    },
  ],
};

interface ApiMockOptions {
  usersByToken?: Record<string, unknown>;
  googleCodeDelayMs?: number;
  dailyRadar?: unknown | null;
  activeEtfDaily?: unknown | null;
  analyzeResult?: unknown;
  analyzeResponsesBySymbol?: Record<string, MockResponse>;
  requestLog?: string[];
  requestBodies?: unknown[];
}

interface MockResponse {
  body: unknown;
  status?: number;
  headers?: Record<string, string>;
}

function json(route: Route, body: unknown, status = 200, headers?: Record<string, string>) {
  return route.fulfill({
    status,
    headers,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

export async function installApiMocks(page: Page, options: ApiMockOptions = {}) {
  const dailyRadar = options.dailyRadar === undefined ? null : options.dailyRadar;
  const activeEtfResponse = options.activeEtfDaily === undefined ? null : options.activeEtfDaily;
  const analyzeResult = options.analyzeResult ?? quickAnalyzeResult;

  await page.route("https://accounts.google.com/**", (route) => route.abort());
  await page.route(`${API_ORIGIN}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const pathname = url.pathname;
    options.requestLog?.push(`${method} ${pathname}`);
    if (request.postData()) options.requestBodies?.push(request.postDataJSON());

    if (method === "GET" && pathname === "/auth/me") {
      const authorization = request.headers().authorization ?? "";
      const token = authorization.startsWith("Bearer ") ? authorization.slice("Bearer ".length) : "";
      return json(route, options.usersByToken?.[token] ?? testUser);
    }
    if (method === "POST" && pathname === "/auth/google/code") {
      if (options.googleCodeDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.googleCodeDelayMs));
      }
      return json(route, { access_token: "e2e-google-code-token", user: testUser });
    }
    if (method === "POST" && pathname === "/analyze") {
      const body = request.postDataJSON() as { symbol?: string };
      const symbolResponse = body.symbol ? options.analyzeResponsesBySymbol?.[body.symbol] : undefined;
      return symbolResponse
        ? json(route, symbolResponse.body, symbolResponse.status ?? 200, symbolResponse.headers)
        : json(route, analyzeResult);
    }
    if (method === "GET" && pathname === "/daily-radar/latest") {
      return dailyRadar
        ? json(route, dailyRadar)
        : json(route, { detail: "No public Daily Radar run is available." }, 404);
    }
    if (method === "GET" && pathname === "/active-etf-holdings/daily") {
      return activeEtfResponse
        ? json(route, activeEtfResponse)
        : json(route, { detail: { code: "active_etf_holdings_not_found" } }, 404);
    }

    return json(route, { detail: `Unhandled E2E route: ${method} ${pathname}` }, 404);
  });
}

export async function authenticate(page: Page, theme: "light" | "dark" = "dark") {
  await page.addInitScript(
    ({ selectedTheme }) => {
      localStorage.setItem("auth_token", "e2e-token");
      if (!localStorage.getItem("theme")) localStorage.setItem("theme", selectedTheme);
    },
    { selectedTheme: theme },
  );
}
