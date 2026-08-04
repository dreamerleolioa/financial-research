import type { Page, Route } from "@playwright/test";

const API_ORIGIN = "http://127.0.0.1:8001";

export const testUser = {
  id: 1,
  email: "e2e@example.com",
  name: "E2E Researcher",
  avatar_url: null,
};

export const watchlistItem = {
  id: 1,
  symbol: "3661.TW",
  name: "世芯-KY",
  notes: "等待量縮回測 MA20",
  sort_order: 0,
  created_at: "2026-07-16T09:00:00+08:00",
  updated_at: "2026-07-16T09:00:00+08:00",
};

export const portfolioItem = {
  id: 11,
  symbol: "2330.TW",
  name: "台積電",
  entry_price: 1018,
  quantity: 1000,
  entry_date: "2026-05-08",
  notes: null,
};

function dateDaysAgo(days: number) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

export const closedPortfolioItem = {
  id: 201,
  position_group_id: "closed-tsmc-e2e",
  symbol: "2330.TW",
  name: "台積電",
  entry_price: 968,
  quantity: 600,
  entry_date: dateDaysAgo(30),
  is_active: false,
  exit_date: dateDaysAgo(0),
  exit_price: 1042,
  exit_quantity: 600,
  exit_fees: 890,
  exit_taxes: 1876,
  realized_pnl: 41_634,
  realized_return_pct: 7.17,
  holding_days: 30,
  notes: null,
};

export const legacyTradeReview = {
  id: 301,
  portfolio_id: closedPortfolioItem.id,
  user_id: testUser.id,
  position_group_id: closedPortfolioItem.position_group_id,
  symbol: closedPortfolioItem.symbol,
  review_version: "trade-review-v1",
  review_result: {},
  evidence_payload: {},
  llm_summary: null,
  created_at: "2026-07-16T09:00:00+08:00",
  updated_at: "2026-07-16T09:00:00+08:00",
};

export const currentTradeReview = {
  ...legacyTradeReview,
  review_version: "trade-review-v3",
  updated_at: "2026-07-16T09:05:00+08:00",
};

export const previousTradeReview = {
  ...currentTradeReview,
  review_version: "trade-review-v2",
};

export const futureTradeReview = {
  ...currentTradeReview,
  review_version: "trade-review-v4",
  llm_summary: "future summary",
  updated_at: "2026-07-16T09:10:00+08:00",
};

export const quickAnalyzeResult = {
  snapshot: {
    symbol: "3661.TW",
    current_price: 3100,
    market_current_price: 3120,
    market_current_price_source: "twse_mis",
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

const emptyRiskSummary = {
  version: "1.0",
  portfolio_revision: "e2e-portfolio-revision",
  as_of_date: "2026-07-16",
  portfolio_value: 0,
  total_unrealized_pnl: 0,
  total_at_risk: 0,
  total_at_risk_pct: 0,
  position_risks: [],
  concentration: { by_symbol: [] },
  shared_exposures: [],
  risk_budget_status: {
    status: "available",
    total_at_risk_pct: 0,
    watch_threshold_pct: 3,
    constrained_threshold_pct: 6,
    notes: [],
  },
  data_quality: {
    status: "ok",
    caveats: [],
    price_stale_after_days: 3,
  },
};

export const populatedRiskSummary = {
  ...emptyRiskSummary,
  portfolio_value: 1_085_000,
  total_unrealized_pnl: 67_000,
  total_at_risk: 37_000,
  total_at_risk_pct: 3.41,
  position_risks: [
    {
      symbol: "2330.TW",
      name: "台積電",
      quantity: 1000,
      current_price: 1085,
      entry_price: 1018,
      market_value: 1_085_000,
      unrealized_pnl: 67_000,
      defense_reference: { price: 1048, source: "ma20" },
      auto_defense_prices: { break_20d_low: 1015, break_ma20: 1048, break_ma60: 1006 },
      estimated_risk_amount: 37_000,
      estimated_risk_pct_of_portfolio: 3.41,
      portfolio_weight_pct: 100,
      risk_state: "contained",
      discipline_triggers: [],
      data_quality: { status: "ok", caveats: [] },
    },
  ],
  concentration: {
    by_symbol: [
      {
        type: "symbol",
        key: "2330.TW",
        market_value: 1_085_000,
        pct_of_portfolio: 100,
        status: "elevated",
      },
    ],
  },
};

interface ApiMockOptions {
  usersByToken?: Record<string, unknown>;
  googleCodeDelayMs?: number;
  watchlist?: unknown[];
  portfolio?: unknown[];
  closedPortfolio?: unknown[];
  tradeReviewGet?: unknown;
  tradeReviewPost?: unknown;
  riskSummary?: unknown;
  priceRefreshSummary?: unknown;
  priceRefreshSummaries?: unknown[];
  priceRefreshDelayMs?: number;
  dailyRadar?: unknown | null;
  analyzeResult?: unknown;
  requestLog?: string[];
  requestBodies?: unknown[];
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

export async function installApiMocks(page: Page, options: ApiMockOptions = {}) {
  const watchlist = options.watchlist ?? [];
  const portfolio = options.portfolio ?? [];
  const closedPortfolio = options.closedPortfolio ?? [];
  const riskSummary = options.riskSummary ?? emptyRiskSummary;
  const priceRefreshSummary = options.priceRefreshSummary ?? riskSummary;
  let priceRefreshResponseIndex = 0;
  const dailyRadar = options.dailyRadar === undefined ? null : options.dailyRadar;
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
    if (method === "GET" && pathname === "/watchlist") return json(route, watchlist);
    if (method === "GET" && pathname === "/portfolio") return json(route, portfolio);
    if (method === "GET" && pathname === "/portfolio/closed") return json(route, closedPortfolio);
    if (method === "GET" && /^\/portfolio\/\d+\/review$/.test(pathname)) {
      return options.tradeReviewGet === undefined
        ? json(route, { detail: "尚未建立交易審核" }, 404)
        : json(route, options.tradeReviewGet);
    }
    if (method === "POST" && /^\/portfolio\/\d+\/review$/.test(pathname)) {
      return options.tradeReviewPost === undefined
        ? json(route, { detail: "Unhandled trade review POST" }, 404)
        : json(route, options.tradeReviewPost);
    }
    if (method === "GET" && pathname === "/portfolio/risk-summary") return json(route, riskSummary);
    if (method === "POST" && pathname === "/portfolio/risk-summary/refresh-prices") {
      const queuedSummary = options.priceRefreshSummaries?.[priceRefreshResponseIndex];
      priceRefreshResponseIndex += 1;
      if (options.priceRefreshDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.priceRefreshDelayMs));
      }
      return json(route, queuedSummary ?? priceRefreshSummary);
    }
    if (method === "GET" && pathname === "/portfolio/latest-history") return json(route, {});
    if (method === "GET" && pathname === "/portfolio/decision-context-status") return json(route, {});
    if (method === "GET" && /^\/portfolio\/\d+\/lifecycle-plan$/.test(pathname)) {
      return json(route, {
        portfolio_id: portfolioItem.id,
        position_group_id: "group-11",
        symbol: portfolioItem.symbol,
        thesis: null,
        setup_type: null,
        planned_holding_period: null,
        default_stop_rule: null,
        add_entry_condition: null,
        planned_invalidation: null,
        planned_stop_price: null,
        planned_target_or_scale_out_rule: null,
        planned_risk_amount: null,
        planned_risk_pct: null,
        position_sizing_rationale: null,
        source: null,
        created_after_entry: null,
      });
    }
    if (method === "POST" && pathname === "/analyze") return json(route, analyzeResult);
    if (method === "DELETE" && /^\/portfolio\/\d+$/.test(pathname)) return route.fulfill({ status: 204 });
    if (method === "GET" && pathname === "/daily-radar/latest") {
      return dailyRadar
        ? json(route, dailyRadar)
        : json(route, { detail: "No public Daily Radar run is available." }, 404);
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
