import { expect, test } from "@playwright/test";
import {
  authenticate,
  closedPortfolioItem,
  currentTradeReview,
  futureTradeReview,
  installApiMocks,
  legacyTradeReview,
  previousTradeReview,
  populatedRiskSummary,
  portfolioItem,
  quickAnalyzeResult,
  radarRun,
  watchlistItem,
} from "./fixtures";

test("Analyze quick lookup supports copy and a keyboard-contained add-position dialog", async ({ page, context }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, { analyzeResult: quickAnalyzeResult });

  await page.goto("/analyze");
  const symbolInput = page.getByRole("textbox", { name: "股票代碼" });
  await symbolInput.fill("3661.TW");
  await page.getByRole("button", { name: "快速資料" }).click();

  await expect(page.getByText("世芯-KY 3661.TW", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("漲停", { exact: true })).toBeVisible();
  await expect(page.getByText("3120（TWSE MIS 即時）", { exact: true })).toBeVisible();
  await expect(page.getByText("今日開／高／低", { exact: true })).toBeVisible();
  await expect(page.getByText("3075 / 3155 / 3050", { exact: true })).toBeVisible();
  await expect(page.getByText("20／60 日均成交量", { exact: true })).toBeVisible();
  await expect(page.getByText("2,100 / 1,800", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "複製技術指標摘要" }).click();
  await expect(page.getByRole("button", { name: "複製技術指標摘要" })).toContainText("已複製");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("3661.TW");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("今日開／高／低：3075 / 3155 / 3050");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("20／60 日均成交量：2,100 / 1,800");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("現價：3120（TWSE MIS 即時）（漲停）");

  const openDialogButton = page.getByRole("button", { name: "加入持股" });
  await openDialogButton.click();
  const dialog = page.getByRole("dialog", { name: "加入我的持股" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "關閉加入持股視窗" })).toBeFocused();

  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "確認新增" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "關閉加入持股視窗" })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(openDialogButton).toBeFocused();
});

test("Analyze presents a bearish directional score without calling it low consistency", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    analyzeResult: {
      ...quickAnalyzeResult,
      analysis: "多維偏空訊號測試。",
      confidence_score: 13,
      data_confidence: 50,
      action_plan: {
        ...quickAnalyzeResult.action_plan,
        conviction_level: "low",
      },
      cross_validation_note: "技術、籌碼與消息皆偏空。",
    },
  });

  await page.goto("/analyze");
  await page.getByRole("textbox", { name: "股票代碼" }).fill("3661.TW");
  await page.getByRole("button", { name: "完整 AI 分析" }).click();

  await expect(page.getByText("綜合訊號強度", { exact: true })).toBeVisible();
  await expect(page.getByText("強烈偏空", { exact: true })).toBeVisible();
  await expect(page.getByText("低一致性", { exact: true })).toHaveCount(0);
  await expect(page.getByText("資料不足 50%", { exact: true })).toBeVisible();
  await expect(page.getByText("訊號分數", { exact: true })).toBeVisible();
  await expect(page.getByText("13 / 100", { exact: true })).toBeVisible();
  await expect(page.getByText("13%", { exact: true })).toHaveCount(0);
});

test("Watchlist quick lookup preserves the copy-to-AI workflow", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, {
    watchlist: [watchlistItem],
    analyzeResult: {
      ...quickAnalyzeResult,
      snapshot: {
        ...quickAnalyzeResult.snapshot,
        current_price: 2580,
        market_current_price: 2555,
        price_limit_quote_price: 2555,
        price_limit_status: "limit_down",
      },
    },
  });

  await page.goto("/watchlist");
  await page.getByRole("button", { name: "技術快查" }).click();
  await expect(page.getByText("跌停", { exact: true })).toBeVisible();
  await expect(page.getByText("2555（TWSE MIS 即時）", { exact: true })).toBeVisible();
  const copyButton = page.getByRole("button", { name: "複製 世芯-KY 3661.TW 技術指標" });
  await expect(copyButton).toBeVisible();
  await copyButton.click();
  await expect(copyButton).toContainText("已複製");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("3661.TW");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("今日開／高／低：3075 / 3155 / 3050");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("20／60 日均成交量：2,100 / 1,800");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("現價：2555（TWSE MIS 即時）（跌停）");
});

test("Portfolio destructive action requires confirmation before DELETE", async ({ page }) => {
  const requestLog: string[] = [];
  await page.setViewportSize({ width: 375, height: 812 });
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    requestLog,
  });

  await page.goto("/portfolio");
  await page.getByRole("button", { name: "開啟 台積電 2330.TW 更多操作" }).click();
  await page.getByRole("button", { name: "刪除持股" }).click();

  const dialog = page.getByRole("alertdialog", { name: "刪除 台積電 2330.TW 持股" });
  await expect(dialog).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();
  expect(requestLog).not.toContain("DELETE /portfolio/11");

  await page.getByRole("button", { name: "開啟 台積電 2330.TW 更多操作" }).click();
  await page.getByRole("button", { name: "刪除持股" }).click();
  await page.getByRole("button", { name: "確認刪除" }).click();
  await expect.poll(() => requestLog).toContain("DELETE /portfolio/11");
});

test("Portfolio refreshes prices without triggering AI analysis", async ({ page }) => {
  const requestLog: string[] = [];
  const refreshedRiskSummary = {
    ...populatedRiskSummary,
    portfolio_value: 1_100_000,
    total_unrealized_pnl: 82_000,
    total_at_risk: 52_000,
    total_at_risk_pct: 4.7273,
    price_refresh: {
      status: "complete",
      requested_count: 1,
      refreshed_count: 1,
      failed_count: 0,
      refreshed_symbols: ["2330.TW"],
      failed_symbols: [],
      refreshed_at: "2026-07-31T10:30:00+08:00",
    },
    position_risks: [
      {
        ...populatedRiskSummary.position_risks[0],
        current_price: 1100,
        market_value: 1_100_000,
        unrealized_pnl: 82_000,
        estimated_risk_amount: 52_000,
        estimated_risk_pct_of_portfolio: 4.7273,
        price_context: {
          refresh_status: "refreshed",
          source: "yfinance_fast_info",
          as_of: "2026-07-31T10:30:00+08:00",
          data_date: "2026-07-31",
          market_session: "intraday",
          is_final: false,
        },
      },
    ],
  };

  await page.setViewportSize({ width: 375, height: 812 });
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    priceRefreshSummary: refreshedRiskSummary,
    requestLog,
  });

  await page.goto("/portfolio");
  const position = page.locator('[data-portfolio-position-id="11"]');
  await expect(position.getByRole("button", { name: "AI 分析" })).toBeVisible();
  await expect(page.getByRole("button", { name: "更新全部價格" })).toBeVisible();

  await position.getByRole("button", { name: "更新 台積電 2330.TW 最新價格" }).click();

  await expect.poll(() => requestLog).toContain("POST /portfolio/risk-summary/refresh-prices");
  await expect(position).toContainText("+8.06%");
  await expect(position).toContainText("現價 1100");
  await expect(position).toContainText("盤中價格 10:30");
  await expect(page.getByRole("status")).toContainText("已更新 1 筆價格");
  expect(requestLog).not.toContain("POST /analyze/position");
});

test("Portfolio loads fresh prices on first visit without a manual refresh", async ({ page }) => {
  const requestLog: string[] = [];
  const refreshedRiskSummary = {
    ...populatedRiskSummary,
    portfolio_value: 1_100_000,
    total_unrealized_pnl: 82_000,
    position_risks: [
      {
        ...populatedRiskSummary.position_risks[0],
        current_price: 1100,
        market_value: 1_100_000,
        unrealized_pnl: 82_000,
        price_context: {
          refresh_status: "refreshed",
          source: "yfinance_fast_info",
          as_of: "2026-07-31T10:30:00+08:00",
          data_date: "2026-07-31",
          market_session: "intraday",
          is_final: false,
        },
      },
    ],
  };

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    priceRefreshSummary: refreshedRiskSummary,
    requestLog,
  });

  await page.goto("/portfolio");

  const position = page.locator('[data-portfolio-position-id="11"]');
  await expect.poll(() => requestLog).toContain("POST /portfolio/risk-summary/refresh-prices");
  await expect(position).toContainText("現價 1100");
  await expect(position).toContainText("盤中價格 10:30");
  expect(requestLog).not.toContain("POST /analyze/position");
});

test("Portfolio refetches fresh prices after a write fails", async ({ page }) => {
  const requestLog: string[] = [];
  const refreshedRiskSummary = {
    ...populatedRiskSummary,
    portfolio_value: 1_100_000,
    total_unrealized_pnl: 82_000,
    price_refresh: {
      status: "complete",
      requested_count: 1,
      refreshed_count: 1,
      failed_count: 0,
      refreshed_symbols: ["2330.TW"],
      failed_symbols: [],
      refreshed_at: "2026-07-31T10:30:00+08:00",
    },
    position_risks: [
      {
        ...populatedRiskSummary.position_risks[0],
        current_price: 1100,
        market_value: 1_100_000,
        unrealized_pnl: 82_000,
        price_context: {
          refresh_status: "refreshed",
          source: "yfinance_fast_info",
          as_of: "2026-07-31T10:30:00+08:00",
          data_date: "2026-07-31",
          market_session: "intraday",
          is_final: false,
        },
      },
    ],
  };

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    priceRefreshSummary: refreshedRiskSummary,
    requestLog,
  });

  await page.goto("/portfolio");
  const position = page.locator('[data-portfolio-position-id="11"]');
  await position.getByRole("button", { name: "更新 台積電 2330.TW 最新價格" }).click();
  await expect(position).toContainText("現價 1100");

  await position.getByRole("button", { name: "開啟 台積電 2330.TW 更多操作" }).click();
  await page.getByRole("button", { name: "編輯持股與計畫" }).click();
  const dialog = page.getByRole("dialog", { name: "編輯 台積電 2330.TW 持股與計畫" });
  await dialog.getByRole("button", { name: "儲存持股與計畫" }).click();

  await expect(dialog).toContainText("Unhandled E2E route: PUT /portfolio/11");
  await expect(position).toContainText("現價 1100");
  await expect
    .poll(() => requestLog.filter((entry) => entry === "POST /portfolio/risk-summary/refresh-prices").length)
    .toBeGreaterThanOrEqual(2);
});

test("Portfolio preserves earlier row refreshes when another row is refreshed", async ({ page }) => {
  const otcItem = {
    ...portfolioItem,
    id: 12,
    symbol: "6488.TWO",
    name: "環球晶",
    entry_price: 690,
    quantity: 10,
  };
  const otcRisk = {
    ...populatedRiskSummary.position_risks[0],
    symbol: "6488.TWO",
    name: "環球晶",
    quantity: 10,
    current_price: 700,
    entry_price: 690,
    market_value: 7000,
    unrealized_pnl: 100,
    defense_reference: { price: 650, source: "planned_stop_price" },
    estimated_risk_amount: 200,
    estimated_risk_pct_of_portfolio: 0.0184,
    portfolio_weight_pct: 0.184,
  };
  const initialSummary = {
    ...populatedRiskSummary,
    portfolio_value: 1_092_000,
    total_unrealized_pnl: 67_100,
    position_risks: [...populatedRiskSummary.position_risks, otcRisk],
  };
  const firstRefreshSummary = {
    ...initialSummary,
    portfolio_value: 1_102_000,
    price_refresh: {
      status: "complete",
      requested_count: 1,
      refreshed_count: 1,
      failed_count: 0,
      refreshed_symbols: ["2330.TW"],
      failed_symbols: [],
      refreshed_at: "2026-07-31T10:30:00+08:00",
    },
    position_risks: [
      {
        ...populatedRiskSummary.position_risks[0],
        current_price: 1100,
        market_value: 1_100_000,
        unrealized_pnl: 82_000,
        price_context: {
          refresh_status: "refreshed",
          source: "yfinance_fast_info",
          as_of: "2026-07-31T10:30:00+08:00",
          data_date: "2026-07-31",
          market_session: "intraday",
          is_final: false,
        },
      },
      otcRisk,
    ],
  };
  const secondRefreshSummary = {
    ...firstRefreshSummary,
    portfolio_value: 1_107_100,
    price_refresh: {
      status: "complete",
      requested_count: 2,
      refreshed_count: 2,
      failed_count: 0,
      refreshed_symbols: ["2330.TW", "6488.TWO"],
      failed_symbols: [],
      refreshed_at: "2026-07-31T10:31:00+08:00",
    },
    position_risks: [
      {
        ...firstRefreshSummary.position_risks[0],
        current_price: 1105,
        market_value: 1_105_000,
        unrealized_pnl: 87_000,
        price_context: {
          ...firstRefreshSummary.position_risks[0].price_context,
          as_of: "2026-07-31T10:31:00+08:00",
        },
      },
      {
        ...otcRisk,
        current_price: 710,
        market_value: 7100,
        unrealized_pnl: 200,
        price_context: {
          refresh_status: "refreshed",
          source: "yfinance_fast_info",
          as_of: "2026-07-31T10:31:00+08:00",
          data_date: "2026-07-31",
          market_session: "intraday",
          is_final: false,
        },
      },
    ],
  };
  const failedPreservedRefreshSummary = {
    ...secondRefreshSummary,
    price_refresh: {
      status: "partial",
      requested_count: 2,
      refreshed_count: 1,
      failed_count: 1,
      refreshed_symbols: ["2330.TW"],
      failed_symbols: ["6488.TWO"],
      refreshed_at: "2026-07-31T10:32:00+08:00",
    },
    position_risks: [
      {
        ...secondRefreshSummary.position_risks[0],
        current_price: 1110,
        market_value: 1_110_000,
      },
      otcRisk,
    ],
  };
  const requestBodies: unknown[] = [];

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem, otcItem],
    riskSummary: initialSummary,
    priceRefreshSummaries: [
      firstRefreshSummary,
      firstRefreshSummary,
      secondRefreshSummary,
      failedPreservedRefreshSummary,
    ],
    requestBodies,
  });

  await page.goto("/portfolio");
  const tsmcPosition = page.locator('[data-portfolio-position-id="11"]');
  const otcPosition = page.locator('[data-portfolio-position-id="12"]');

  await expect(tsmcPosition).toContainText("現價 1100");

  await page.getByRole("link", { name: "個股分析" }).first().click();
  await page.getByRole("link", { name: "持股管理" }).first().click();
  await expect(tsmcPosition).toContainText("現價 1100");

  await otcPosition.getByRole("button", { name: "更新 環球晶 6488.TWO 最新價格" }).click();
  await expect(otcPosition).toContainText("現價 710");
  await expect(tsmcPosition).toContainText("現價 1105");

  await tsmcPosition.getByRole("button", { name: "更新 台積電 2330.TW 最新價格" }).click();
  await expect(page.getByRole("status")).toContainText("為保留先前更新價格");
  await expect(tsmcPosition).toContainText("現價 1105");
  await expect(otcPosition).toContainText("現價 710");
  expect(requestBodies).toEqual([
    { portfolio_ids: null },
    { portfolio_ids: null },
    { portfolio_ids: [11, 12] },
    { portfolio_ids: [11, 12] },
  ]);
});

test("Portfolio ignores a late price response after portfolio state changes", async ({ page }) => {
  const requestLog: string[] = [];
  const summaryAt1090 = {
    ...populatedRiskSummary,
    price_refresh: {
      status: "complete",
      requested_count: 1,
      refreshed_count: 1,
      failed_count: 0,
      refreshed_symbols: ["2330.TW"],
      failed_symbols: [],
      refreshed_at: "2026-07-31T10:30:00+08:00",
    },
    position_risks: [
      {
        ...populatedRiskSummary.position_risks[0],
        current_price: 1090,
        price_context: {
          refresh_status: "refreshed",
          source: "yfinance_fast_info",
          as_of: "2026-07-31T10:30:00+08:00",
          data_date: "2026-07-31",
          market_session: "intraday",
          is_final: false,
        },
      },
    ],
  };
  const lateSummaryAt1100 = {
    ...summaryAt1090,
    position_risks: [
      {
        ...summaryAt1090.position_risks[0],
        current_price: 1100,
      },
    ],
  };
  const latestSummaryAt1110 = {
    ...summaryAt1090,
    position_risks: [
      {
        ...summaryAt1090.position_risks[0],
        current_price: 1110,
        price_context: {
          ...summaryAt1090.position_risks[0].price_context,
          as_of: "2026-07-31T10:31:00+08:00",
        },
      },
    ],
  };

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    priceRefreshSummaries: [summaryAt1090, lateSummaryAt1100, latestSummaryAt1110],
    priceRefreshDelayMs: 150,
    requestLog,
  });

  await page.goto("/portfolio");
  const position = page.locator('[data-portfolio-position-id="11"]');
  await position.getByRole("button", { name: "更新 台積電 2330.TW 最新價格" }).click();
  await expect.poll(() => requestLog).toContain("POST /portfolio/risk-summary/refresh-prices");
  await page.evaluate(() => {
    localStorage.setItem("portfolio_mutation_revision", "changed-in-another-tab");
  });

  await expect(page.getByRole("status")).toContainText("持股資料已變更，本次價格刷新未套用");
  await expect(position).toContainText("現價 1110");
  await expect(position).not.toContainText("現價 1100");
});

test("Closed Portfolio presents a populated realized-PnL group", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { closedPortfolio: [closedPortfolioItem] });

  await page.goto("/portfolio/closed");
  await expect(page.getByRole("heading", { name: "結案回顧" })).toBeVisible();

  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await expect(closedPosition).toContainText("台積電");
  await expect(closedPosition).toContainText("600 股");
  await expect(closedPosition).toContainText("+41,634");
  await expect(closedPosition.getByRole("button", { name: "整體部位檢討" })).toBeVisible();
  await expect(closedPosition.getByRole("button", { name: "事件時間線" })).toBeVisible();
});

test("Closed Portfolio upgrades a saved v1 trade review before presenting it", async ({ page }) => {
  const requestLog: string[] = [];
  await authenticate(page);
  await installApiMocks(page, {
    closedPortfolio: [closedPortfolioItem],
    tradeReviewGet: legacyTradeReview,
    tradeReviewPost: currentTradeReview,
    requestLog,
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "檢討分析" }).click();

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 檢討分析" });
  await expect(dialog).toContainText("trade-review-v3");
  await expect.poll(() => requestLog.filter((entry) => entry === "GET /portfolio/201/review").length).toBe(1);
  await expect.poll(() => requestLog.filter((entry) => entry === "POST /portfolio/201/review").length).toBe(1);
});

test("Closed Portfolio upgrades a saved v2 trade review before presenting it", async ({ page }) => {
  const requestLog: string[] = [];
  await authenticate(page);
  await installApiMocks(page, {
    closedPortfolio: [closedPortfolioItem],
    tradeReviewGet: previousTradeReview,
    tradeReviewPost: currentTradeReview,
    requestLog,
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "檢討分析" }).click();

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 檢討分析" });
  await expect(dialog).toContainText("trade-review-v3");
  await expect.poll(() => requestLog.filter((entry) => entry === "GET /portfolio/201/review").length).toBe(1);
  await expect.poll(() => requestLog.filter((entry) => entry === "POST /portfolio/201/review").length).toBe(1);
});

test("Closed Portfolio refreshes a saved v3 trade review before presenting it", async ({ page }) => {
  const requestLog: string[] = [];
  await authenticate(page);
  await installApiMocks(page, {
    closedPortfolio: [closedPortfolioItem],
    tradeReviewGet: currentTradeReview,
    tradeReviewPost: currentTradeReview,
    requestLog,
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "檢討分析" }).click();

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 檢討分析" });
  await expect(dialog).toContainText("trade-review-v3");
  await expect.poll(() => requestLog.filter((entry) => entry === "GET /portfolio/201/review").length).toBe(1);
  await expect.poll(() => requestLog.filter((entry) => entry === "POST /portfolio/201/review").length).toBe(1);
});

test("Closed Portfolio retries a concurrent trade review refresh using Retry-After", async ({ page }) => {
  const requestLog: string[] = [];
  await authenticate(page);
  await installApiMocks(page, {
    closedPortfolio: [closedPortfolioItem],
    tradeReviewGet: currentTradeReview,
    tradeReviewPostSequence: [
      {
        body: { detail: "交易審核正在更新中，請稍後重試" },
        status: 409,
        headers: { "Retry-After": "0" },
      },
      { body: currentTradeReview },
    ],
    requestLog,
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "檢討分析" }).click();

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 檢討分析" });
  await expect(dialog).toContainText("trade-review-v3");
  await expect.poll(() => requestLog.filter((entry) => entry === "POST /portfolio/201/review").length).toBe(2);
});

test("Closed Portfolio does not downgrade an unknown newer trade review", async ({ page }) => {
  const requestLog: string[] = [];
  await authenticate(page);
  await installApiMocks(page, {
    closedPortfolio: [closedPortfolioItem],
    tradeReviewGet: futureTradeReview,
    requestLog,
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "檢討分析" }).click();

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 檢討分析" });
  await expect(dialog).toContainText("trade-review-v4");
  await expect.poll(() => requestLog.filter((entry) => entry === "GET /portfolio/201/review").length).toBe(1);
  expect(requestLog.filter((entry) => entry === "POST /portfolio/201/review")).toHaveLength(0);
});

test("Daily Radar detail drawer traps focus and restores it on Escape", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: radarRun });

  await page.goto("/daily-radar");
  const openDrawerButton = page.getByRole("button", { name: "查看細節" });
  await openDrawerButton.click();

  const drawer = page.getByRole("dialog", { name: "台積電 · 2330.TW" });
  await expect(drawer).toBeVisible();
  await expect(page.getByRole("button", { name: "關閉候選追蹤細節" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("link", { name: "前往單股完整分析" })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(openDrawerButton).toBeFocused();
});
