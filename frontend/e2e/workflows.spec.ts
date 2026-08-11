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

test("Portfolio close records exit reason, plan adherence, and confidence", async ({ page }) => {
  let closeRequestBody: Record<string, unknown> | null = null;
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
  });
  await page.route("http://127.0.0.1:8001/portfolio/11/close", async (route) => {
    closeRequestBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...portfolioItem, is_active: false }),
    });
  });

  await page.goto("/portfolio");
  await page.getByRole("button", { name: "開啟 台積電 2330.TW 更多操作" }).click();
  await page.getByRole("button", { name: "結案持股" }).click();

  const dialog = page.getByRole("dialog", { name: "結案 台積電 2330.TW 批次" });
  await dialog.getByLabel("結案價格").fill("950");
  await dialog.getByLabel("結案原因").selectOption("stop_loss");
  await expect(dialog.getByLabel("是否符合原計畫").locator('option[value="no"]')).toHaveText("不符合原始計畫");
  await dialog.getByLabel("是否符合原計畫").selectOption("no");
  await dialog.getByLabel("決策信心").selectOption("medium");
  await dialog.getByRole("button", { name: "確認結案" }).click();

  await expect
    .poll(() => closeRequestBody)
    .toMatchObject({
      exit_price: 950,
      exit_quantity: 1000,
      reason_code: "stop_loss",
      plan_adherence: "no",
      confidence_level: "medium",
    });
});

test("Portfolio prepares and copies neutral technical snapshots for every holding", async ({ page, context }) => {
  const requestLog: string[] = [];
  const requestBodies: unknown[] = [];
  const secondPortfolioItem = {
    ...portfolioItem,
    id: 12,
    symbol: "6488.TWO",
    name: "環球晶",
    entry_price: 690,
    quantity: 200,
    entry_date: "2026-06-12",
  };
  const riskSummary = {
    ...populatedRiskSummary,
    position_risks: [
      { ...populatedRiskSummary.position_risks[0], portfolio_weight_pct: 80 },
      {
        ...populatedRiskSummary.position_risks[0],
        symbol: secondPortfolioItem.symbol,
        name: secondPortfolioItem.name,
        quantity: secondPortfolioItem.quantity,
        current_price: 700,
        entry_price: secondPortfolioItem.entry_price,
        market_value: 140_000,
        unrealized_pnl: 2_000,
        defense_reference: { price: 650, source: "planned_stop_price" },
        portfolio_weight_pct: 20,
      },
    ],
  };

  await page.setViewportSize({ width: 375, height: 812 });
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem, secondPortfolioItem],
    riskSummary,
    analyzeResponsesBySymbol: {
      "2330.TW": {
        body: {
          ...quickAnalyzeResult,
          snapshot: {
            ...quickAnalyzeResult.snapshot,
            symbol: "2330.TW",
            current_price: 1085,
            market_current_price: 1085,
            price_limit_status: "normal",
          },
          symbol_name: "台積電",
        },
      },
      "6488.TWO": {
        body: {
          ...quickAnalyzeResult,
          snapshot: {
            ...quickAnalyzeResult.snapshot,
            symbol: "6488.TWO",
            current_price: 700,
            market_current_price: 700,
            price_limit_status: "normal",
          },
          symbol_name: "環球晶",
        },
      },
    },
    requestLog,
    requestBodies,
  });

  await page.goto("/portfolio");
  const prepareButton = page.getByRole("button", { name: "整理全部技術資料" });
  await expect(prepareButton).toBeEnabled();
  await prepareButton.click();

  await expect(page.getByText("已整理 2/2 檔技術資料")).toBeVisible();
  await page.getByRole("button", { name: "複製全部持股技術資料" }).click();
  await expect(page.getByRole("button", { name: "複製全部持股技術資料" })).toContainText("已複製");

  const copiedText = await page.evaluate(() => navigator.clipboard.readText());
  expect(copiedText).toMatch(/^技術指標摘要\n持股成本：1018\n進場日期：2026-05-08\n持有股數：1000\n股票名稱：台積電/);
  expect(copiedText).toContain("持股成本：1018");
  expect(copiedText).toContain("進場日期：2026-05-08");
  expect(copiedText).toContain("持有股數：1000");
  expect(copiedText).toContain("股票名稱：台積電");
  expect(copiedText).toContain("股票名稱：環球晶");
  expect(copiedText.match(/\n\n---\n\n/g)).toHaveLength(1);
  expect(copiedText).toContain("均線 MA5/20/60");
  expect(copiedText).not.toContain("全部持股技術資料");
  expect(copiedText).not.toContain("產生時間：");
  expect(copiedText).not.toContain("資料範圍：");
  expect(copiedText).not.toContain("整理結果：");
  expect(copiedText).not.toContain("持股 1/2");
  expect(copiedText).not.toContain("持股權重：");
  expect(copiedText).not.toContain("目前損益率：");
  expect(copiedText).not.toContain("防守參考：");
  expect(copiedText).not.toContain("技術資料日：");
  expect(copiedText).not.toContain("technical_score");

  const technicalBodies = requestBodies.filter(
    (body): body is { symbol: string; skip_ai: boolean } =>
      typeof body === "object" && body !== null && "symbol" in body && "skip_ai" in body,
  );
  expect(technicalBodies).toHaveLength(2);
  expect(technicalBodies.map((body) => body.symbol).sort()).toEqual(["2330.TW", "6488.TWO"]);
  expect(technicalBodies.every((body) => body.skip_ai === true)).toBe(true);
  expect(requestLog).not.toContain("POST /analyze/position");

  await page.evaluate(() => {
    localStorage.setItem("portfolio_mutation_revision", "changed-after-technical-lookup");
  });
  await page.getByRole("button", { name: "複製全部持股技術資料" }).click();
  await expect(page.getByText("持股資料已變更，請重新整理技術資料")).toBeVisible();
  await expect(page.getByRole("button", { name: "複製全部持股技術資料" })).toHaveCount(0);
});

test("Portfolio reloads position facts after a cross-tab mutation before technical lookup", async ({
  page,
  context,
}) => {
  const updatedPortfolioItem = {
    ...portfolioItem,
    entry_price: 1018.25,
    entry_date: "2026-07-01",
    quantity: 900,
  };
  let updatedItemsRequestCount = 0;
  let releaseUpdatedItems: (() => void) | undefined;
  const updatedItemsGate = new Promise<void>((resolve) => {
    releaseUpdatedItems = resolve;
  });

  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    analyzeResponsesBySymbol: {
      "2330.TW": {
        body: {
          ...quickAnalyzeResult,
          snapshot: { ...quickAnalyzeResult.snapshot, symbol: "2330.TW" },
          symbol_name: "台積電",
        },
      },
    },
  });
  await page.goto("/portfolio");
  const prepareButton = page.getByRole("button", { name: "整理全部技術資料" });
  await expect(prepareButton).toBeEnabled();

  await page.route("http://127.0.0.1:8001/portfolio", async (route) => {
    updatedItemsRequestCount += 1;
    await updatedItemsGate;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([updatedPortfolioItem]) });
  });
  await page.evaluate(() => {
    const oldValue = localStorage.getItem("portfolio_mutation_revision");
    const newValue = "changed-in-another-tab-before-technical-lookup";
    localStorage.setItem("portfolio_mutation_revision", newValue);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "portfolio_mutation_revision",
        oldValue,
        newValue,
      }),
    );
  });

  await expect.poll(() => updatedItemsRequestCount).toBe(1);
  await expect(prepareButton).toBeDisabled();
  releaseUpdatedItems?.();
  await expect(prepareButton).toBeEnabled();
  await prepareButton.click();
  await expect(page.getByText("已整理 1/1 檔技術資料")).toBeVisible();
  await page.getByRole("button", { name: "複製全部持股技術資料" }).click();

  const copiedText = await page.evaluate(() => navigator.clipboard.readText());
  expect(copiedText).toContain("持股成本：1018.25");
  expect(copiedText).toContain("進場日期：2026-07-01");
  expect(copiedText).toContain("持有股數：900");
  expect(copiedText).not.toContain("持股成本：1020");
});

test("Portfolio keeps technical lookup disabled when a cross-tab items reload fails", async ({ page }) => {
  const requestLog: string[] = [];
  let failedItemsRequestCount = 0;

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    requestLog,
  });
  await page.goto("/portfolio");
  const prepareButton = page.getByRole("button", { name: "整理全部技術資料" });
  await expect(prepareButton).toBeEnabled();

  await page.route("http://127.0.0.1:8001/portfolio", async (route) => {
    failedItemsRequestCount += 1;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "portfolio unavailable" }),
    });
  });
  await page.evaluate(() => {
    const oldValue = localStorage.getItem("portfolio_mutation_revision");
    const newValue = "changed-in-another-tab-with-failed-items-reload";
    localStorage.setItem("portfolio_mutation_revision", newValue);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "portfolio_mutation_revision",
        oldValue,
        newValue,
      }),
    );
  });

  await expect.poll(() => failedItemsRequestCount, { timeout: 5000 }).toBe(2);
  await page.waitForTimeout(100);
  await expect(prepareButton).toBeDisabled();
  expect(requestLog).not.toContain("POST /analyze");
});

test("Portfolio keeps invalidated technical workers locked until their requests settle", async ({ page }) => {
  const portfolio = [
    portfolioItem,
    { ...portfolioItem, id: 12, symbol: "6488.TWO", name: "環球晶" },
    { ...portfolioItem, id: 13, symbol: "2454.TW", name: "聯發科" },
    { ...portfolioItem, id: 14, symbol: "2308.TW", name: "台達電" },
  ];
  const updatedPortfolio = portfolio.map((item, index) =>
    index === 0 ? { ...item, entry_price: item.entry_price + 0.25 } : item,
  );
  let activeAnalyzeRequests = 0;
  let maxActiveAnalyzeRequests = 0;
  let analyzeRequestCount = 0;
  let releaseFirstBatch: (() => void) | undefined;
  let updatedItemsRequestCount = 0;
  let releaseUpdatedItems: (() => void) | undefined;
  const firstBatchGate = new Promise<void>((resolve) => {
    releaseFirstBatch = resolve;
  });
  const updatedItemsGate = new Promise<void>((resolve) => {
    releaseUpdatedItems = resolve;
  });

  await authenticate(page);
  await installApiMocks(page, {
    portfolio,
    riskSummary: populatedRiskSummary,
  });
  await page.goto("/portfolio");
  const prepareButton = page.getByRole("button", { name: "整理全部技術資料" });
  await expect(prepareButton).toBeEnabled();

  await page.route("http://127.0.0.1:8001/analyze", async (route) => {
    const requestNumber = ++analyzeRequestCount;
    activeAnalyzeRequests += 1;
    maxActiveAnalyzeRequests = Math.max(maxActiveAnalyzeRequests, activeAnalyzeRequests);
    try {
      if (requestNumber <= 3) await firstBatchGate;
      const body = route.request().postDataJSON() as { symbol: string };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...quickAnalyzeResult,
          snapshot: { ...quickAnalyzeResult.snapshot, symbol: body.symbol },
        }),
      });
    } finally {
      activeAnalyzeRequests -= 1;
    }
  });
  await page.route("http://127.0.0.1:8001/portfolio", async (route) => {
    updatedItemsRequestCount += 1;
    await updatedItemsGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(updatedPortfolio),
    });
  });

  await prepareButton.click();
  await expect.poll(() => analyzeRequestCount).toBe(3);
  expect(maxActiveAnalyzeRequests).toBe(3);
  await page.evaluate(() => {
    const oldValue = localStorage.getItem("portfolio_mutation_revision");
    const newValue = "changed-while-technical-workers-are-running";
    localStorage.setItem("portfolio_mutation_revision", newValue);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "portfolio_mutation_revision",
        oldValue,
        newValue,
      }),
    );
  });

  await expect.poll(() => updatedItemsRequestCount).toBe(1);
  await expect(page.getByRole("button", { name: /技術整理中/ })).toBeDisabled();
  releaseFirstBatch?.();
  await expect(page.getByText("持股資料已變更，請重新整理技術資料")).toBeVisible();
  await page.waitForTimeout(100);
  expect(analyzeRequestCount).toBe(3);

  const rerunButton = page.getByRole("button", { name: "重新整理技術資料" });
  await expect(rerunButton).toBeDisabled();
  releaseUpdatedItems?.();
  const readyRerunButton = page.getByRole("button", { name: /^(整理全部技術資料|重新整理技術資料)$/ });
  await expect(readyRerunButton).toBeEnabled();
  await readyRerunButton.click();
  await expect(page.getByText("已整理 4/4 檔技術資料")).toBeVisible();
  expect(analyzeRequestCount).toBe(7);
  expect(maxActiveAnalyzeRequests).toBe(3);
});

test("Portfolio treats a structured 200 analysis error as a failed technical holding", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
    analyzeResponsesBySymbol: {
      "2330.TW": {
        body: {
          ...quickAnalyzeResult,
          snapshot: { ...quickAnalyzeResult.snapshot, symbol: "2330.TW" },
          technical_indicators: {},
          errors: [{ code: "ANALYZE_RUNTIME_ERROR", message: "provider unavailable" }],
        },
      },
    },
  });
  await page.goto("/portfolio");
  await page.getByRole("button", { name: "整理全部技術資料" }).click();

  await expect(page.getByText("技術資料整理失敗：2330.TW")).toBeVisible();
  await expect(page.getByRole("button", { name: "複製全部持股技術資料" })).toHaveCount(0);
});

test("Portfolio keeps successful technical data copyable when one holding fails", async ({ page, context }) => {
  const secondPortfolioItem = {
    ...portfolioItem,
    id: 12,
    symbol: "6488.TWO",
    name: "環球晶",
  };

  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem, secondPortfolioItem],
    riskSummary: populatedRiskSummary,
    analyzeResponsesBySymbol: {
      "2330.TW": {
        body: {
          ...quickAnalyzeResult,
          snapshot: {
            ...quickAnalyzeResult.snapshot,
            symbol: "2330.TW",
            current_price: 1085,
            market_current_price: 1085,
            price_limit_status: "normal",
          },
          symbol_name: "台積電",
        },
      },
      "6488.TWO": { body: { detail: "provider unavailable" }, status: 503 },
    },
  });

  await page.goto("/portfolio");
  const prepareButton = page.getByRole("button", { name: "整理全部技術資料" });
  await expect(prepareButton).toBeEnabled();
  await prepareButton.click();

  await expect(page.getByText("已整理 1/2 檔，失敗：6488.TWO")).toBeVisible();
  await page.getByRole("button", { name: "複製全部持股技術資料" }).click();
  const copiedText = await page.evaluate(() => navigator.clipboard.readText());
  expect(copiedText).toContain("股票代碼：2330.TW");
  expect(copiedText).not.toContain("\n\n---\n\n");
  expect(copiedText).not.toContain("失敗標的：6488.TWO");
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
  await expect(position).toContainText("成本 1018");
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
    latestHistory: {
      "11": {
        record_date: "2026-07-30",
        action_tag: "Exit",
        signal_confidence: 72,
        recommended_action: "Exit",
        indicators: null,
        risk_state: "critical",
        risk_state_label: "防守條件已觸發",
        discipline_triggers: ["法人籌碼轉弱，需檢查風險。"],
        risk_control_reference: { reference_price: 1025 },
        compatibility_source: "position_risk_language",
      },
    },
    requestLog,
  });

  await page.goto("/portfolio");

  const position = page.locator('[data-portfolio-position-id="11"]');
  const positionHeader = page.locator("[data-portfolio-position-header]");
  await expect.poll(() => requestLog).toContain("POST /portfolio/risk-summary/refresh-prices");
  await expect(position).toContainText("現價 1100");
  await expect(position).toContainText("盤中價格 10:30");
  await expect(positionHeader).toContainText("狀態／計畫");
  await expect(positionHeader).toContainText("防守緩衝");
  await expect(position).toContainText("防守可控");
  await expect(position).toContainText("尚有 4.73%");
  await expect(position).toContainText("計畫防守價 1048");
  await expect(position).toContainText("AI 2026-07-30");
  await expect(position).toContainText("風險檢查已觸發");
  await expect(position).not.toContainText("防守條件已觸發");

  const [headerColumns, rowColumns, rowCellTops] = await Promise.all([
    positionHeader.evaluate((element) => getComputedStyle(element).gridTemplateColumns),
    position.evaluate((element) => getComputedStyle(element).gridTemplateColumns),
    position.evaluate((element) =>
      Array.from(element.children)
        .slice(0, 6)
        .map((child) => Math.round(child.getBoundingClientRect().top)),
    ),
  ]);
  expect(rowColumns).toBe(headerColumns);
  expect(new Set(rowCellTops).size).toBe(1);
  expect(requestLog).not.toContain("POST /analyze/position");
});

test("Portfolio keeps the position list readable at 1024px", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: populatedRiskSummary,
  });

  await page.goto("/portfolio");

  const position = page.locator('[data-portfolio-position-id="11"]');
  await expect(position).toBeVisible();
  await expect(page.locator("[data-portfolio-position-header]")).toBeHidden();
  await expect(position.getByText("未實現損益", { exact: true })).toBeVisible();
  await expect(position.getByText("防守緩衝", { exact: true })).toBeVisible();
  await expect(position.getByRole("button", { name: "AI 分析" })).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBe(dimensions.clientWidth);
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

test("Closed Portfolio presents a neutral lifecycle classification without a missing-context warning", async ({
  page,
}) => {
  let decisionContextStatus = "present";
  let backfilledPlan = false;
  let insufficientData: string[] = [];
  await authenticate(page);
  await installApiMocks(page, { closedPortfolio: [closedPortfolioItem] });
  await page.route("http://127.0.0.1:8001/portfolio/groups/closed-tsmc-e2e/lifecycle-review", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: 301,
        user_id: 1,
        position_group_id: "closed-tsmc-e2e",
        symbol: "2330.TW",
        review_version: "position-lifecycle-review-v3",
        review_result: {
          lifecycle_metrics: {},
          entry_sequence: {},
          exit_sequence: {},
          advanced_internal: {},
          event_indicator_snapshots: [],
          event_facts: [
            {
              event_key: "id:1",
              event_type: "initial_entry",
              event_date: "2026-05-08",
              price: 1018,
              quantity: 600,
              source: "user_recorded_at_event_time",
            },
          ],
          decision_context: {
            status: decisionContextStatus,
            has_plan: true,
            historical_judgment_eligible: !backfilledPlan,
            source: backfilledPlan ? "user_backfilled" : "user_recorded_at_event_time",
            created_after_entry: backfilledPlan,
            planned_holding_period: "swing",
            default_stop_rule: "fixed_price",
            add_entry_condition: "data_quality_complete_only",
          },
          data_quality: {
            status: insufficientData.length > 0 ? "insufficient" : "ok",
            notes: insufficientData.map((key) => `Missing ${key}`),
            insufficient_data: insufficientData,
          },
          lifecycle_review: {
            classification: {
              primary_label: insufficientData.length > 0 ? "insufficient_data" : "unclassified",
              labels: [insufficientData.length > 0 ? "insufficient_data" : "unclassified"],
              tier: insufficientData.length > 0 ? "insufficient_context" : "mixed",
              reasons: [{ text: "目前沒有命中既定生命週期分類。", source_refs: ["lifecycle_metrics"] }],
              caveats: [],
              source_refs: ["lifecycle_metrics"],
            },
            overall_conclusion: { text: "目前沒有命中既定生命週期分類。", source_refs: ["lifecycle_metrics"] },
            what_worked: [],
            what_needs_review: [],
            event_level_evidence: [],
            next_operation_rules: [],
            data_quality_notes: [],
          },
        },
        evidence_payload: {},
        llm_summary: null,
        created_at: "2026-08-11T00:00:00Z",
        updated_at: "2026-08-11T00:00:00Z",
      }),
    });
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "整體部位檢討" }).click();

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 整體部位檢討" });
  await expect(dialog).toContainText("position-lifecycle-review-v3");
  const overallSection = dialog.locator("article").filter({ hasText: "整體結果" });
  await expect(overallSection.getByText("暫無適用分類", { exact: true })).toHaveCount(1);
  await expect(dialog).not.toContainText("混合結論");
  await expect(dialog).not.toContainText("原始計畫缺失：");
  await expect(dialog).not.toContainText("事件或市場證據不足");

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  decisionContextStatus = "retrospective_only";
  backfilledPlan = true;
  insufficientData = ["full_exit_2026-05-10_ma20"];
  await closedPosition.getByRole("button", { name: "整體部位檢討" }).click();

  await expect(dialog).toContainText("檢討證據不足");
  await expect(dialog).toContainText("事件或市場證據不足");
  await expect(dialog).toContainText("事後補填計畫提示");
  await expect(dialog).not.toContainText("原始計畫缺失：");
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
