import { expect, test } from "@playwright/test";
import { authenticate, installApiMocks, radarRun } from "./fixtures";
import type { DailyRadarObservationStats, DailyRadarValidationResponse } from "../src/lib/dailyRadarTypes";

const stats: DailyRadarObservationStats = {
  signal_date_count: 10,
  selected_count: 30,
  evaluated_observation_count: 20,
  evaluated_signal_date_count: 8,
  evaluated_distinct_symbol_count: 20,
  missing_outcome_count: 0,
  missing_diagnostic_count: 0,
  immature_observation_count: 5,
  excluded_repeat_count: 5,
  ranking_pool_complete: true,
  skipped_validation_count: 0,
  status_counts: { confirmed: 12, invalidated: 3, unconfirmed: 5 },
  missing_reasons: {},
  coverage_complete: true,
  confirmation_rate: 0.6,
  invalidation_rate: 0.15,
  mean_lead_trading_days: 3,
  mean_waiting_max_adverse_excursion_pct: -2.5,
  means_scope: "evaluated_first_observations_only",
};

const windowGroups = {
  all_selected: stats,
  top_3: stats,
  remaining_after_3: { ...stats, confirmation_rate: 0.4 },
  top_5: { ...stats, confirmation_rate: 0.5 },
  remaining_after_5: { ...stats, confirmation_rate: 0.3 },
};

const validationFixture: DailyRadarValidationResponse = {
  diagnostic_version: "daily-radar-observation-v1",
  as_of_date: "2026-07-16",
  sample_start_date: "2026-04-18",
  sample_end_date: "2026-07-16",
  lookback_days: 90,
  calendar_through_date: "2026-07-16",
  last_evaluated_date: "2026-07-15",
  default_cohort_id: "current",
  cohorts: [
    {
      id: "current",
      strategy: { scoring_version: "s1", rule_version: "r1", config_version: "c1", selection_version: "v1" },
      signal_start_date: "2026-06-01",
      signal_end_date: "2026-07-16",
      windows: {
        "5": windowGroups,
        "10": { ...windowGroups, top_3: { ...stats, confirmation_rate: 0.7 } },
        "20": windowGroups,
      },
    },
  ],
};

const qualityStats = {
  sample_count: 10, evaluated_count: 8, signal_date_count: 4, distinct_symbol_count: 6,
  missing_outcome_count: 0, skipped_count: 0, immature_count: 2, missing_metric_count: 0,
  benchmark_symbols: ["TAIEX"], coverage_complete: true, positive_excess_count: 6,
  positive_excess_rate: .75, median_excess_return_pct: 2.5,
};

test("Validation supports 40 and 60 trading-day windows and requests longer history", async ({ page }) => {
  const body = structuredClone(validationFixture);
  body.cohorts[0].windows["40"] = windowGroups;
  body.cohorts[0].windows["60"] = Object.fromEntries(Object.entries(windowGroups).map(([key, value]) =>
    [key, { ...value, confirmation_rate: .8 }]));
  await setup(page, body);
  await page.route("**/daily-radar/validation?lookback_days=365", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ ...body, lookback_days: 365 }),
  }));
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  await page.getByRole("button", { name: "40 日", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
  await page.getByRole("button", { name: "60 日", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("80.0%");
  await page.getByLabel("驗證樣本期間").selectOption("365");
  await expect(page.getByText("樣本範圍（最近 365 日）", { exact: true })).toBeVisible();
});

test("Pool quality compares saved selected and shadow samples with explicit scope", async ({ page }) => {
  const body = structuredClone(validationFixture);
  body.cohorts[0].pool_comparison = { "5": {
    selected: qualityStats, top_3: qualityStats, top_5: qualityStats,
    comparable_shadow: { ...qualityStats, positive_excess_rate: .5, median_excess_return_pct: -.5 },
    population_scope: "observed_daily_comparable_pool", observed_positive_capture_share: .6,
  } };
  await setup(page, body);
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  const panel = page.getByRole("heading", { name: "候選池選股與排序品質 · 5 日" }).locator("..");
  await expect(panel).toContainText("非獨立交易樣本");
  await expect(panel).toContainText("非全市場召回率");
  await expect(panel.getByRole("row", { name: /可比較未入選/ })).toContainText("50.0%");
  await expect(panel.getByRole("row", { name: /可比較未入選/ })).toContainText("-0.5%");
  await expect(page.getByTestId("pool-capture-share")).toHaveText("60.0%");
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: test.info().outputPath("candidate-pool-quality-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "10 日", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("70.0%");
  await expect(page.getByTestId("pool-capture-share")).toHaveCount(0);
});

test("Missing pool outcomes keep rates unavailable", async ({ page }) => {
  const body = structuredClone(validationFixture);
  const missing = { ...qualityStats, missing_outcome_count: 1, coverage_complete: false,
    positive_excess_rate: null, median_excess_return_pct: null };
  body.cohorts[0].pool_comparison = { "5": {
    selected: missing, top_3: missing, top_5: missing, comparable_shadow: qualityStats,
    population_scope: "observed_daily_comparable_pool", observed_positive_capture_share: null,
  } };
  await setup(page, body);
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  const panel = page.getByRole("heading", { name: "候選池選股與排序品質 · 5 日" }).locator("..");
  await expect(panel.getByRole("row", { name: /全部入池/ })).toContainText("資料不足");
  await expect(page.getByTestId("pool-capture-share")).toHaveText("資料不足或尚待累積");
});

async function setup(page: Parameters<typeof authenticate>[0], body: unknown = validationFixture) {
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: radarRun });
  await page.route("**/daily-radar/validation", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    }),
  );
  await page.goto("/daily-radar");
}

test("Validation is a lazy read-only tab with period and priority comparisons", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/daily-radar/validation")) requests.push(req.method());
  });
  await setup(page);
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  await expect(page.getByRole("heading", { name: "突破前觀察驗證" })).toBeVisible();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
  await page.getByRole("button", { name: "10 日", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("70.0%");
  await page.getByRole("button", { name: "每日前 5 檔", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("50.0%");
  await expect(page.getByRole("columnheader", { name: "每日前 5 檔" })).toBeVisible();
  await page.getByRole("tab", { name: "觀察名單", exact: true }).click();
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  expect(requests).toEqual(["GET"]);
});

test("Validation explains missing and immature data without displaying false zero rates", async ({ page }) => {
  const body = structuredClone(validationFixture);
  const empty = {
    ...stats,
    evaluated_observation_count: 0,
    confirmation_rate: null,
    invalidation_rate: null,
    coverage_complete: false,
    missing_diagnostic_count: 12,
    immature_observation_count: 8,
    mean_lead_trading_days: null,
    mean_waiting_max_adverse_excursion_pct: null,
    status_counts: {},
    missing_reasons: {},
  };
  body.cohorts[0].windows["5"] = Object.fromEntries(Object.keys(windowGroups).map((key) => [key, empty]));
  await setup(page, body);
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  await expect(page.getByText("尚未累積可評估結果", { exact: true })).toBeVisible();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("資料不足");
  await expect(page.getByText("尚未計算突破診斷", { exact: true })).toBeVisible();
  await expect(page.getByText("資料尚未涵蓋完整觀察期", { exact: true })).toBeVisible();
  await expect(page.getByText("0.0%", { exact: true })).toHaveCount(0);
});

test("Validation handles no history and a first read failure with retry", async ({ page }) => {
  await setup(page, { ...validationFixture, cohorts: [], default_cohort_id: null });
  await page.route("**/daily-radar/validation", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "temporary failure" }),
    }),
  );
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  await expect(page.getByText("驗證結果暫時無法載入", { exact: true })).toBeVisible();
  await page.unroute("**/daily-radar/validation");
  await page.route("**/daily-radar/validation", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...validationFixture, cohorts: [], default_cohort_id: null }),
    }),
  );
  await page.getByRole("button", { name: "重新讀取驗證結果" }).click();
  await expect(page.getByText("目前沒有可統計的觀察批次", { exact: true })).toBeVisible();
});

test("Validation preserves previous statistics during refresh and refresh failure", async ({ page }) => {
  await setup(page);
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/daily-radar/validation", async (route) => {
    await pending;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "failed" }) });
  });
  await page.getByRole("button", { name: "重新整理", exact: true }).click();
  try {
    await expect(page.getByRole("button", { name: "讀取中…", exact: true })).toBeVisible();
    await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
  } finally {
    release();
  }
  await expect(page.getByText("更新失敗，以下保留上次成功讀取的驗證結果。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
});

test("Validation renders at desktop and mobile widths without page overflow", async ({ page }) => {
  await setup(page);
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page.getByRole("heading", { name: "突破前觀察驗證" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`daily-radar-validation-${width}.png`), fullPage: true });
  }
});

test("Validation keeps strategy cohorts separate and supports keyboard tab navigation", async ({ page }) => {
  const old = structuredClone(validationFixture.cohorts[0]);
  old.id = "old";
  old.strategy.selection_version = "old-v1";
  old.windows["5"].top_3.confirmation_rate = 0.2;
  await setup(page, { ...validationFixture, cohorts: [...validationFixture.cohorts, old] });
  const observations = page.getByRole("tab", { name: "觀察名單", exact: true });
  await observations.focus();
  await observations.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "驗證結果", exact: true })).toBeFocused();
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
  await page.getByLabel("策略批次（不同版本分開統計）").selectOption("old");
  await expect(page.getByTestId("confirmation-rate")).toHaveText("20.0%");
  await page.getByLabel("策略批次（不同版本分開統計）").selectOption("current");
  await expect(page.getByTestId("confirmation-rate")).toHaveText("60.0%");
});

test("Validation shows initial loading and labels means from incomplete coverage", async ({ page }) => {
  const body = structuredClone(validationFixture);
  body.cohorts[0].windows["5"].top_3.coverage_complete = false;
  body.cohorts[0].windows["5"].top_3.confirmation_rate = null;
  await setup(page, body);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/daily-radar/validation", async (route) => {
    await pending;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.getByRole("tab", { name: "驗證結果", exact: true }).click();
  try {
    await expect(page.getByText("正在讀取驗證結果…", { exact: true })).toBeVisible();
  } finally {
    release();
  }
  await expect(page.getByTestId("confirmation-rate")).toHaveText("資料不足");
  await expect(page.getByText("部分樣本 · 僅計已確認突破，按交易日計算", { exact: true })).toBeVisible();
  await expect(page.getByText("部分樣本 · 以首次訊號收盤至等待結束期間低點計算", { exact: true })).toBeVisible();
});
