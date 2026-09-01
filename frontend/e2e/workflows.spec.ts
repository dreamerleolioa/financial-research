import { expect, test } from "@playwright/test";
import {
  activeEtfDaily,
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

test("Active ETF tracking filters funds, shows consensus, and restores drawer focus", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  await expect(page.getByText("2 / 5", { exact: true })).toBeVisible();
  await expect(page.getByText("有來源缺口", { exact: true })).toBeVisible();
  await expect(
    page.getByText("單一來源且已有前次快照也會發布變化；前後兩期都經雙來源確認時，會另外標註。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "查看 2330.TW 台積電 持股變化" })).toHaveCount(2);
  await expect(page.getByRole("table").getByText("雙來源確認", { exact: true })).toHaveCount(2);

  await page
    .getByRole("button", { name: /00985A/ })
    .first()
    .click();
  await expect(page.getByText("本期來源 2026-08-28", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看 2330.TW 台積電 持股變化" })).toHaveCount(1);
  await expect(page.getByRole("table").getByText("2454.TW", { exact: true })).toBeVisible();
  await expect(page.getByText("2317.TW", { exact: true })).toHaveCount(0);

  const openDrawerButton = page.getByRole("button", { name: "查看 2454.TW 聯發科 持股變化" });
  await openDrawerButton.click();
  const drawer = page.getByRole("dialog", { name: "2454.TW 聯發科" });
  await expect(drawer).toContainText("可能受基金規模變動影響");
  await expect(drawer.getByText("比較含單一來源", { exact: true })).toBeVisible();
  const currentEvidence = drawer.getByRole("region", { name: "本期證據 2026-08-28" });
  const previousEvidence = drawer.getByRole("region", { name: "前期證據 2026-08-27" });
  await expect(currentEvidence.getByText("雙來源確認", { exact: true })).toBeVisible();
  await expect(currentEvidence.getByText("發行投信官方資料", { exact: true })).toBeVisible();
  await expect(currentEvidence.getByText("MoneyDJ", { exact: true })).toBeVisible();
  await expect(previousEvidence.getByText("單一來源", { exact: true })).toBeVisible();
  await expect(previousEvidence.getByText("MoneyDJ", { exact: true })).toBeVisible();
  await expect(previousEvidence.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "關閉持股變化明細" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(openDrawerButton).toBeFocused();

  await page.getByRole("button", { name: /00982A/ }).click();
  await expect(page.getByText("尚未接上完整的發行投信官方持股來源", { exact: true })).toBeVisible();
  const openSingleSourceDrawerButton = page.getByRole("button", { name: "查看 2881.TW 富邦金 持股變化" });
  await expect(openSingleSourceDrawerButton).toBeVisible();
  await openSingleSourceDrawerButton.click();
  const singleSourceDrawer = page.getByRole("dialog", { name: "2881.TW 富邦金" });
  await expect(singleSourceDrawer.getByText("比較含單一來源", { exact: true })).toBeVisible();
  await expect(singleSourceDrawer.getByText("雙來源確認", { exact: true })).toHaveCount(0);
  await expect(singleSourceDrawer.getByRole("region", { name: "本期證據 2026-08-28" })).toContainText("MoneyDJ");
  await expect(singleSourceDrawer.getByRole("region", { name: "前期證據 2026-08-27" })).toContainText("MoneyDJ");
  await expect(singleSourceDrawer.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /00983A/ }).click();
  await expect(page.getByText("兩個來源的持股代碼或股數不一致", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "個股共識" }).click();
  await expect(page.getByText("共同增加", { exact: true })).toBeVisible();
  await expect(page.getByText("2 檔共識", { exact: true })).toBeVisible();
  await expect(page.getByText("單一基金增加", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("1 檔基金", { exact: true }).first()).toBeVisible();
});

test("Active ETF tracking resets the selected fund when search is cleared", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  await page
    .getByRole("button", { name: /00985A/ })
    .first()
    .click();

  const search = page.getByRole("searchbox", { name: "搜尋標的或基金" });
  await search.fill("2454");
  await expect(page.getByText("已顯示 1 / 1 筆", { exact: true })).toBeVisible();

  await search.fill("");
  await expect(page.getByText("已顯示 5 / 5 筆", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "全部基金 5" })).toHaveAttribute("aria-pressed", "true");
});

test("Active ETF tracking can clear the selected fund from its summary", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 700 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  await page
    .getByRole("button", { name: /00985A/ })
    .first()
    .click();
  await expect(page.getByText("本期來源 2026-08-28", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "查看全部基金" }).click();

  await expect(page.getByRole("button", { name: "全部基金 5" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "查看全部基金" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看 2330.TW 台積電 持股變化" })).toHaveCount(2);
});

test("Active ETF tracking can clear an unavailable date from the error state", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily: {} });

  await page.goto("/active-etf?date=2026-08-28");
  await expect(page.getByRole("heading", { name: "持股變化暫時無法載入", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "查看最新資料" }).click();

  await expect(page).toHaveURL(/\/active-etf$/);
});

test("Active ETF tracking keeps source labels readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  const verifiedCard = page.locator("article").filter({
    has: page.getByRole("button", { name: "查看 2317.TW 鴻海 持股變化" }),
  });
  const singleSourceCard = page.locator("article").filter({
    has: page.getByRole("button", { name: "查看 2881.TW 富邦金 持股變化" }),
  });
  await expect(verifiedCard.getByText("雙來源確認", { exact: true })).toBeVisible();
  await expect(singleSourceCard.getByText("雙來源確認", { exact: true })).toHaveCount(0);
  await expect(page.getByText("單一來源", { exact: true }).locator("..").getByText("1", { exact: true })).toBeVisible();

  await page.getByRole("combobox", { name: "基金" }).selectOption("00985A");
  await expect(page.getByRole("button", { name: "查看全部基金" })).toBeVisible();
  expect(await page.locator("body").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

  await page.getByRole("button", { name: "查看 2454.TW 聯發科 持股變化" }).click();
  const mobileDrawer = page.getByRole("dialog", { name: "2454.TW 聯發科" });
  await expect(mobileDrawer.getByRole("region", { name: "本期證據 2026-08-28" })).toBeAttached();
  await expect(mobileDrawer.getByRole("region", { name: "前期證據 2026-08-27" })).toBeAttached();
  expect(await mobileDrawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "個股共識" }).click();
  await expect(page.getByText("2 檔共識", { exact: true })).toBeVisible();
  await expect(page.getByText("1 檔基金", { exact: true }).first()).toBeVisible();
});

test("Active ETF consensus labels multi-fund direction conflicts without overstating consensus", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      consensus: activeEtfDaily.consensus.map((item, index) =>
        index === 0
          ? {
              ...item,
              direction: "mixed",
              added_count: 1,
              decreased_count: 1,
            }
          : item,
      ),
    },
  });

  await page.goto("/active-etf");
  await page.getByRole("button", { name: "個股共識" }).click();
  await expect(page.getByText("2 檔方向分歧", { exact: true })).toBeVisible();
  await expect(page.getByText("2 檔共識", { exact: true })).toHaveCount(0);
});

test("Active ETF tracking accepts the previous verified-only API contract during deployment", async ({ page }) => {
  const legacyChanges = activeEtfDaily.changes
    .slice(0, 4)
    .map((change) =>
      Object.fromEntries(
        Object.entries(change).filter(([key]) => !["verification_status", "source_count"].includes(key)),
      ),
    );
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      covered_funds: 2,
      summary: {
        ...activeEtfDaily.summary,
        changed_funds: 2,
        changed_stocks: 3,
        changed_rows: 4,
        increases: 1,
      },
      funds: activeEtfDaily.funds.map((fund) => {
        const legacyFund = Object.fromEntries(Object.entries(fund).filter(([key]) => key !== "evidence_periods"));
        return fund.fund_code === "00982A"
          ? { ...legacyFund, status: "single_source", previous_date: null, change_count: 0, common_scale_ratio: null }
          : legacyFund;
      }),
      changes: legacyChanges,
      consensus: activeEtfDaily.consensus.slice(0, 1),
    },
  });

  await page.goto("/active-etf");
  await expect(page.getByRole("table").getByText("雙來源確認", { exact: true })).toHaveCount(4);
  await page.getByRole("button", { name: "查看 2454.TW 聯發科 持股變化" }).click();
  const drawer = page.getByRole("dialog", { name: "2454.TW 聯發科" });
  await expect(
    drawer.getByText("此 API 版本未提供分期來源明細；為避免混淆，不顯示目前期來源作為整段比較證據。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(drawer.getByText("MoneyDJ", { exact: true })).toHaveCount(0);
  await expect(drawer.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
});

test("Active ETF tracking reveals large change sets in bounded batches", async ({ page }) => {
  const baseChange = activeEtfDaily.changes[0];
  const changes = Array.from({ length: 101 }, (_, index) => ({
    ...baseChange,
    symbol: `${String(index + 1).padStart(4, "0")}.TW`,
    name: `批次標的 ${index + 1}`,
  }));
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      summary: {
        ...activeEtfDaily.summary,
        changed_funds: 1,
        changed_rows: changes.length,
        changed_stocks: changes.length,
      },
      funds: activeEtfDaily.funds.map((fund, index) =>
        index === 0 ? { ...fund, change_count: changes.length } : { ...fund, change_count: 0 },
      ),
      changes,
    },
  });

  await page.goto("/active-etf");
  await expect(page.getByText("已顯示 100 / 101 筆", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "顯示更多" }).click();
  await expect(page.getByText("已顯示 101 / 101 筆", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "顯示更多" })).toHaveCount(0);
});

test("Active ETF tracking rejects malformed decimal fields at the API boundary", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      changes: [{ ...activeEtfDaily.changes[0], current_weight_pct: "" }],
    },
  });

  await page.goto("/active-etf");
  await expect(page.getByRole("heading", { name: "持股變化暫時無法載入", level: 2 })).toBeVisible();
  await expect(page.getByText("新增持股", { exact: true })).toHaveCount(0);
});

test("Active ETF tracking rejects changes attributed to a conflicted fund", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      changes: [{ ...activeEtfDaily.changes[0], fund_code: "00983A", fund_name: "測試來源衝突基金" }],
    },
  });

  await page.goto("/active-etf");
  await expect(page.getByRole("heading", { name: "持股變化暫時無法載入", level: 2 })).toBeVisible();
  await expect(page.getByText("新增持股", { exact: true })).toHaveCount(0);
});

test("Active ETF tracking rejects evidence attached to the wrong comparison date", async ({ page }) => {
  const currentFund = activeEtfDaily.funds[0];
  const previousEvidence = currentFund.evidence_periods[1];
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      funds: activeEtfDaily.funds.map((fund, index) =>
        index === 0
          ? {
              ...fund,
              evidence_periods: [
                currentFund.evidence_periods[0],
                {
                  ...previousEvidence,
                  sources: previousEvidence.sources.map((source) => ({ ...source, data_date: "2026-08-28" })),
                },
              ],
            }
          : fund,
      ),
    },
  });

  await page.goto("/active-etf");
  await expect(page.getByRole("heading", { name: "持股變化暫時無法載入", level: 2 })).toBeVisible();
  await expect(page.getByText("新增持股", { exact: true })).toHaveCount(0);
});

test("Analyze deterministic research supports copy and a keyboard-contained add-position dialog", async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, { analyzeResult: quickAnalyzeResult });

  await page.goto("/analyze");
  const symbolInput = page.getByRole("textbox", { name: "股票代碼" });
  await symbolInput.fill("3661.TW");
  await page.getByRole("button", { name: "開始分析" }).click();

  await expect(page.getByText("世芯-KY 3661.TW", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("漲停", { exact: true })).toBeVisible();
  await expect(page.getByText("3120（TWSE MIS 即時）", { exact: true })).toBeVisible();
  await expect(page.getByText("今日開／高／低", { exact: true })).toBeVisible();
  await expect(page.getByText("3075 / 3155 / 3050", { exact: true })).toBeVisible();
  await expect(page.getByText("20／60 日均成交量", { exact: true })).toBeVisible();
  await expect(page.getByText("2,100 / 1,800", { exact: true })).toBeVisible();
  await expect(page.getByText("MA20 5日斜率", { exact: true })).toBeVisible();
  await expect(page.getByText("+1.234%", { exact: true })).toBeVisible();
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
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("MA20 5日斜率：+1.234%");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).not.toContain("波動狀態");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).not.toContain("訊號衝突");

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
  await page.getByRole("button", { name: /開始分析/ }).click();

  await expect(page.getByText("綜合訊號強度", { exact: true })).toBeVisible();
  await expect(page.getByText("強烈偏空", { exact: true })).toBeVisible();
  await expect(page.getByText("低一致性", { exact: true })).toHaveCount(0);
  await expect(page.getByText("資料不足 50%", { exact: true })).toBeVisible();
  await expect(page.getByText("訊號分數", { exact: true })).toBeVisible();
  await expect(page.getByText("13 / 100", { exact: true })).toBeVisible();
  await expect(page.getByText("13%", { exact: true })).toHaveCount(0);
});

test("Analyze presents a user-facing failure without backend codes or exception text", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    analyzeResult: {
      ...quickAnalyzeResult,
      errors: [{ code: "CRAWL_ERROR", message: "yfinance connection reset by peer" }],
    },
  });

  await page.goto("/analyze");
  await page.getByRole("textbox", { name: "股票代碼" }).fill("3661.TW");
  await page.getByRole("button", { name: /開始分析/ }).click();

  await expect(page.getByText("無法取得這檔股票的市場資料，請稍後再試。")).toBeVisible();
  await expect(page.getByText(/CRAWL_ERROR|yfinance|connection reset/)).toHaveCount(0);
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

test("Watchlist localizes an unknown AVWAP data gap without exposing its code", async ({ page }) => {
  const internalReason = "new_phase1_provider_failure";
  await authenticate(page);
  await installApiMocks(page, {
    watchlist: [watchlistItem],
    analyzeResult: {
      ...quickAnalyzeResult,
      phase1_observation: {
        symbol: "3661.TW",
        data_date: "2026-07-16",
        dataset: "TaiwanStockPrice",
        adjustment_mode: "unadjusted",
        freshness: "missing",
        missing_reason: internalReason,
        source: {
          provider: "phase1_avwap_snapshot",
          dataset: "TaiwanStockPrice",
          adjustment_mode: "unadjusted",
        },
        source_granularity: "daily",
        anchors: {},
        data_quality: { blocking: false, missing_reason: internalReason },
      },
    },
  });

  await page.goto("/watchlist");
  await page.getByRole("button", { name: "技術快查" }).click();

  await expect(page.getByText("AVWAP 資料不足").first()).toBeVisible();
  await expect(page.getByText("2026-07-16 · 未還原價格")).toBeVisible();
  await expect(page.getByText(internalReason)).toHaveCount(0);
  await expect(page.getByText("unadjusted")).toHaveCount(0);
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

test("Portfolio records cash and labels partial shared-exposure coverage", async ({ page }) => {
  const requestLog: string[] = [];
  const requestBodies: unknown[] = [];
  const partialSummary = {
    ...populatedRiskSummary,
    account_capital: {
      status: "recorded",
      cash_balance: 500_000,
      invested_market_value: 1_085_000,
      account_equity: 1_585_000,
      cash_pct_of_account_equity: 31.5457,
      invested_pct_of_account_equity: 68.4543,
      risk_percentage_denominator: "account_equity",
    },
    concentration: {
      ...populatedRiskSummary.concentration,
      by_industry: [
        {
          type: "industry",
          key: "半導體業",
          symbols: ["2330.TW"],
          market_value: 1_085_000,
          pct_of_invested: 100,
          pct_of_capital_base: 68.4543,
          status: "partial",
        },
      ],
      industry_coverage: {
        status: "partial",
        classified_market_value: 1_085_000,
        pct_of_invested: 100,
        eligible_position_count: 2,
        valued_position_count: 1,
        classified_position_count: 1,
        unvalued_position_count: 1,
        unclassified_valued_position_count: 0,
      },
    },
    correlation_risk: {
      status: "partial",
      minimum_overlapping_return_count: 20,
      eligible_position_count: 3,
      valued_position_count: 2,
      possible_pair_count: 3,
      eligible_pair_count: 1,
      pair_coverage_pct: 33.3333,
      weighted_average_correlation: 0.72,
      watch_threshold: 0.65,
      elevated_threshold: 0.8,
      pairs: [
        {
          symbols: ["2330.TW", "2317.TW"],
          correlation: 0.72,
          overlapping_return_count: 20,
          combined_invested_weight_pct: 100,
          status: "watch",
        },
      ],
      interpretation: "descriptive_co_movement_not_forward_prediction",
    },
  };
  const updatedSummary = {
    ...partialSummary,
    account_capital: {
      ...partialSummary.account_capital,
      cash_balance: 600_000,
      account_equity: 1_685_000,
      cash_pct_of_account_equity: 35.6083,
      invested_pct_of_account_equity: 64.3917,
    },
  };

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: partialSummary,
    priceRefreshSummaries: [partialSummary, updatedSummary],
    requestLog,
    requestBodies,
  });

  await page.goto("/portfolio");
  await page.getByRole("button", { name: "展開風險細節" }).click();

  await expect(page.getByText(/已分類 1 \/ 2 檔/)).toBeVisible();
  await expect(page.getByText(/覆蓋不足，暫不定級/)).toBeVisible();
  await expect(page.getByText(/可計算組合 1 \/ 3/)).toBeVisible();
  await expect(page.getByText(/僅部分持股組合具備足夠且可估值/)).toBeVisible();

  await page.getByLabel("可用現金餘額").fill("600000");
  await page.getByRole("button", { name: "儲存", exact: true }).click();

  await expect.poll(() => requestLog).toContain("PUT /portfolio/account-settings");
  expect(requestBodies).toContainEqual({ cash_balance: 600000 });
  await expect(page.getByText(/現金.*600,000/)).toBeVisible();
  await expect(page.getByLabel("可用現金餘額")).toHaveValue("600000");
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
    (body): body is { symbol: string; persist_result: boolean } =>
      typeof body === "object" && body !== null && "symbol" in body && "persist_result" in body,
  );
  expect(technicalBodies).toHaveLength(2);
  expect(technicalBodies.map((body) => body.symbol).sort()).toEqual(["2330.TW", "6488.TWO"]);
  expect(technicalBodies.every((body) => body.persist_result === false)).toBe(true);
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
  const diagnoseButton = position.getByRole("button", { name: "持倉診斷" });
  const updatePriceButton = position.getByRole("button", { name: "更新 台積電 2330.TW 最新價格" });
  await expect(diagnoseButton).toBeVisible();
  for (const button of [updatePriceButton, diagnoseButton]) {
    await expect(button).toHaveCSS("white-space", "nowrap");
    expect(await button.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
  await expect(page.getByRole("button", { name: "更新全部價格" })).toBeVisible();

  await updatePriceButton.click();

  await expect.poll(() => requestLog).toContain("POST /portfolio/risk-summary/refresh-prices");
  await expect(position).toContainText("+8.06%");
  await expect(position).toContainText("現價 1100");
  await expect(position).toContainText("成本 1018");
  await expect(position).toContainText("盤中價格 10:30");
  await expect(page.getByRole("status")).toContainText("已更新 1 筆價格");
  expect(requestLog).not.toContain("POST /analyze/position");
});

test("Portfolio AVWAP observation explains one consistent price basis without internal rule codes", async ({
  page,
}) => {
  const avwapRiskSummary = {
    ...populatedRiskSummary,
    phase1_current_day_lists: {
      version: "phase1-current-day-lists-v1",
      implemented_lists: ["holding_management_candidates", "holding_risk_alerts"],
      pending_lists: [
        "pullback_observation_candidates",
        "breakout_confirmation_candidates",
        "overheated_do_not_chase_candidates",
      ],
      pullback_observation_candidates: [],
      breakout_confirmation_candidates: [],
      holding_management_candidates: [
        {
          symbol: "2330.TW",
          name: "台積電",
          label: "續抱",
          position_state: "hold",
          close: 1085,
          price_context: {
            refresh_status: "refreshed",
            source: "yfinance_fast_info",
            as_of: "2026-07-31T10:30:00+08:00",
            data_date: "2026-07-31",
            market_session: "intraday",
            is_final: false,
          },
          holding_avg_cost: 1018,
          avwap_data_date: "2026-07-30",
          display_anchor: {
            type: "entry",
            anchor_date: "2026-07-01",
            avwap: 1050,
            distance_to_avwap_pct: 3.3333,
            distance_basis: "portfolio_current_price",
            distance_price: 1085,
            distance_price_data_date: "2026-07-31",
            distance_price_as_of: "2026-07-31T10:30:00+08:00",
          },
          matched_rules: ["phase1_display_anchor_supported"],
          current_day_observation: "最新價格仍位於「進場後 AVWAP」之上，尚未跌破這條技術觀察線。",
          data_quality: { blocking: false },
        },
      ],
      holding_risk_alerts: [],
      overheated_do_not_chase_candidates: [],
    },
  };

  await authenticate(page);
  await installApiMocks(page, {
    portfolio: [portfolioItem],
    riskSummary: avwapRiskSummary,
    priceRefreshSummary: avwapRiskSummary,
  });

  await page.goto("/portfolio");
  await page.getByRole("button", { name: "展開風險細節" }).click();

  const section = page.locator("[data-phase1-observations]");
  await expect(section).toContainText("進場後 AVWAP");
  await expect(section).toContainText("盤中報價 10:30");
  await expect(section).toContainText("AVWAP 資料日 2026-07-30");
  await expect(section).toContainText("AVWAP 觀察線");
  await expect(section).toContainText("1050");
  await expect(section).toContainText("+3.33%");
  await expect(section).toContainText("AVWAP 是技術觀察線，不等同持有成本或正式防守價");
  await expect(section).not.toContainText("phase1_display_anchor_supported");
  await expect(section).not.toContainText("entry", { useInnerText: true });
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
  await expect(position).toContainText("上次診斷 2026-07-30");
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
  for (const button of [
    position.getByRole("button", { name: "更新 台積電 2330.TW 最新價格" }),
    position.getByRole("button", { name: "持倉診斷" }),
  ]) {
    await expect(button).toHaveCSS("white-space", "nowrap");
    expect(await button.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
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
  const diagnoseButton = position.getByRole("button", { name: "持倉診斷" });
  await expect(diagnoseButton).toBeVisible();
  await expect(diagnoseButton).toHaveCSS("white-space", "nowrap");
  expect(await diagnoseButton.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

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
  await expect(closedPosition).toContainText("完整交易");
  await expect(closedPosition.getByRole("button", { name: "查看完整復盤" })).toBeVisible();
  await expect(closedPosition.getByRole("button", { name: "查看這次處分" })).toBeVisible();
});

test("Closed Portfolio keeps every exit batch when the lifecycle completes inside the selected period", async ({
  page,
}) => {
  const dateFromToday = (days: number) => {
    const value = new Date();
    value.setUTCDate(value.getUTCDate() + days);
    return value.toISOString().slice(0, 10);
  };
  const firstExit = {
    ...closedPortfolioItem,
    id: 201,
    exit_date: dateFromToday(-45),
    exit_quantity: 200,
    realized_pnl: 12_000,
    sequence_number: 1,
    display_label: "第 1 次減碼",
    event_id: 501,
    event_type: "partial_exit",
    reason_category: "risk_control",
    reason_code: "profit_protection",
    plan_adherence: "yes",
    confidence_level: "high",
  };
  const finalExit = {
    ...closedPortfolioItem,
    id: 202,
    entry_price: 990,
    exit_date: dateFromToday(0),
    exit_quantity: 400,
    realized_pnl: 29_634,
    sequence_number: 2,
    display_label: "最終出清",
    event_id: 502,
    event_type: "full_exit",
    reason_category: "technical",
    reason_code: "support_broken",
    plan_adherence: "partial",
    confidence_level: "medium",
  };
  await authenticate(page);
  await installApiMocks(page, {
    closedLifecycles: [
      {
        position_group_id: closedPortfolioItem.position_group_id,
        symbol: closedPortfolioItem.symbol,
        name: closedPortfolioItem.name,
        lifecycle_start_date: dateFromToday(-60),
        lifecycle_end_date: dateFromToday(0),
        initial_entry_price: 968,
        entry_event_count: 2,
        add_entry_count: 1,
        exit_event_count: 2,
        total_closed_quantity: 600,
        total_realized_pnl: 41_634,
        exit_batches: [firstExit, finalExit],
      },
    ],
  });

  await page.goto("/portfolio/closed");

  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await expect(closedPosition).toContainText("第 1 次減碼");
  await expect(closedPosition).toContainText("最終出清");
  await expect(closedPosition).toContainText("2 次處分");
  await expect(page.getByText("本期間共 1 筆完整交易")).toBeVisible();
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
            event_level_evidence: [
              {
                text: "2026-05-08 發生 initial_entry；事件當下市場狀態為 uptrend。",
                source_refs: ["event_facts.id:1"],
              },
            ],
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
  await closedPosition.getByRole("button", { name: "查看完整復盤" }).click();

  const workspace = page.locator("#closed-review-workspace");
  await expect(workspace).toBeVisible();
  await workspace.getByText("技術細節與來源").click();
  await expect(workspace).toContainText("position-lifecycle-review-v3");
  const overallSection = workspace.locator("article").filter({ hasText: "整體結果" });
  await expect(overallSection.getByText("暫無適用分類", { exact: true })).toHaveCount(1);
  await expect(workspace).not.toContainText("混合結論");
  await expect(workspace).not.toContainText("原始計畫缺失：");
  await expect(workspace).toContainText("事件當下紀錄");
  await expect(workspace).toContainText("發生 初始進場；事件當下市場狀態為 上升趨勢");
  await expect(workspace).not.toContainText("real events");
  await expect(workspace).not.toContainText("initial_entry");
  await expect(workspace).not.toContainText("uptrend");
  await expect(workspace).not.toContainText("事件當下技術行情覆蓋不足");

  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 完整交易復盤" });
  await dialog.getByRole("button", { name: "關閉完整交易復盤" }).click();
  await expect(workspace).toBeHidden();
  await expect(closedPosition.getByRole("button", { name: "查看完整復盤" })).toBeFocused();
  decisionContextStatus = "retrospective_only";
  backfilledPlan = true;
  insufficientData = ["full_exit_2026-05-10_ma20"];
  await closedPosition.getByRole("button", { name: "查看完整復盤" }).click();

  await expect(workspace).toContainText("檢討證據不足");
  await expect(workspace).toContainText("事件當下技術行情覆蓋不足");
  await expect(workspace).toContainText("完整結案 2026-05-10 的 MA20");
  await expect(workspace).toContainText("不代表你沒有記錄操作原因");
  await expect(workspace).toContainText("事後補填計畫提示");
  await expect(workspace).toContainText("部分資料不完整，請查看下方資料不足欄位");
  await expect(workspace).not.toContainText("Missing full_exit_2026-05-10_ma20");
  await expect(workspace).not.toContainText("原始計畫缺失：");
});

test("Closed Portfolio separates profitable outcome from mixed process quality and actionable feedback", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await authenticate(page);
  await installApiMocks(page, { closedPortfolio: [closedPortfolioItem] });
  await page.route("http://127.0.0.1:8001/portfolio/groups/closed-tsmc-e2e/events", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ position_group_id: "closed-tsmc-e2e", symbol: "2330.TW", events: [] }),
    });
  });
  await page.route("http://127.0.0.1:8001/portfolio/groups/closed-tsmc-e2e/lifecycle-review", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: 401,
        user_id: 1,
        position_group_id: "closed-tsmc-e2e",
        symbol: "2330.TW",
        review_version: "position-lifecycle-review-v4",
        review_result: {
          lifecycle_metrics: {
            total_realized_pnl: 41_634,
            total_return_pct_on_weighted_cost: 7.17,
          },
          entry_sequence: {},
          exit_sequence: {},
          advanced_internal: {},
          event_indicator_snapshots: [],
          event_facts: [],
          decision_context: { status: "present", has_plan: true, historical_judgment_eligible: true },
          data_quality: { status: "ok", notes: [], insufficient_data: [] },
          lifecycle_review: {
            outcome: {
              status: "profit",
              label: "結果獲利",
              summary: "獲利結果不等同於操作流程必然正確。",
              total_realized_pnl: 41_634,
              total_return_pct: 7.17,
              source_refs: ["lifecycle_metrics.total_realized_pnl"],
            },
            process_quality: {
              status: "mixed",
              label: "流程有好有壞",
              summary: "同時有可保留的做法與需要修正的決策。",
              strength_labels: ["disciplined_scale_out"],
              risk_labels: ["late_scale_out"],
              source_refs: ["exit_sequence"],
            },
            dimensions: {
              entry: {
                label: "進場品質",
                status: "future_status",
                summary: "未命中模式。",
                source_refs: ["entry_sequence"],
              },
              position_management: {
                label: "部位管理",
                status: "strength",
                summary: "分批管理可追溯。",
                source_refs: ["exit_sequence"],
              },
              risk_exit: {
                label: "風險與出場",
                status: "mixed",
                summary: "有保護也有延遲。",
                source_refs: ["exit_sequence"],
              },
              record_quality: {
                label: "紀錄品質",
                status: "sufficient",
                summary: "紀錄足夠。",
                source_refs: ["data_quality"],
              },
            },
            feedback: {
              keep: [
                {
                  label: "disciplined_scale_out",
                  title: "保留分批保護獲利",
                  observation: "部分結案先鎖定獲利。",
                  action: "下次沿用事前定義的分批條件。",
                  source_refs: ["exit_sequence.partial_exit_count"],
                },
              ],
              improve: [
                {
                  label: "late_scale_out",
                  title: "提前定義風險出口",
                  observation: "較大比例的結案發生在轉弱之後。",
                  action: "下次進場前先定義破位條件。",
                  source_refs: ["exit_sequence.percentage_sold_after_breakdown"],
                },
              ],
              next_actions: [
                {
                  title: "下次操作規則",
                  action: "破位條件觸發時直接降低曝險。",
                  source_refs: ["exit_sequence.percentage_sold_after_breakdown"],
                },
              ],
            },
            classification: {
              primary_label: "late_scale_out",
              labels: ["disciplined_scale_out", "late_scale_out"],
              tier: "needs_review",
              reasons: [],
              caveats: [],
              source_refs: ["exit_sequence"],
            },
            event_level_evidence: [],
            data_quality_notes: [],
          },
        },
        evidence_payload: {},
        llm_summary: null,
        created_at: "2026-08-26T00:00:00Z",
        updated_at: "2026-08-26T00:00:00Z",
      }),
    });
  });

  await page.goto("/portfolio/closed");
  const closedPosition = page.locator('[data-closed-position-group="closed-tsmc-e2e"]');
  await closedPosition.getByRole("button", { name: "查看完整復盤" }).click();

  const workspace = page.locator("#closed-review-workspace");
  await expect(workspace).toContainText("結果獲利");
  await expect(workspace).toContainText("流程有好有壞");
  await expect(workspace).toContainText("進場品質");
  await expect(workspace).toContainText("部位管理");
  await expect(workspace).toContainText("風險與出場");
  await expect(workspace).toContainText("紀錄品質");
  await expect(workspace).toContainText("其他狀態");
  await expect(workspace).not.toContainText("future_status");
  await expect(workspace).toContainText("保留分批保護獲利");
  await expect(workspace).toContainText("提前定義風險出口");
  await expect(workspace).toContainText("破位條件觸發時直接降低曝險");
  await expect(closedPosition).toContainText("流程有好有壞");
  const dialog = page.getByRole("dialog", { name: "台積電 2330.TW 完整交易復盤" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "關閉完整交易復盤" })).toBeFocused();
  await expect.poll(() => page.locator("#root").evaluate((element) => element.hasAttribute("inert"))).toBe(true);
  await page.keyboard.press("Shift+Tab");
  await expect(workspace.locator("summary").last()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "關閉完整交易復盤" })).toBeFocused();
  await expect.poll(() => workspace.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await workspace.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
  await expect(dialog.getByRole("button", { name: "上一筆完整交易" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("Closed Portfolio switches lifecycle reviews inside the dialog without returning to the list", async ({
  page,
}) => {
  const secondClosedPortfolioItem = {
    ...closedPortfolioItem,
    id: 202,
    position_group_id: "closed-mediatek-e2e",
    symbol: "2454.TW",
    name: "聯發科",
    realized_pnl: -8_500,
  };
  await authenticate(page);
  await installApiMocks(page, { closedPortfolio: [closedPortfolioItem, secondClosedPortfolioItem] });

  await page.goto("/portfolio/closed");
  const firstTrigger = page
    .locator('[data-closed-position-group="closed-tsmc-e2e"]')
    .getByRole("button", { name: "查看完整復盤" });
  await firstTrigger.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("台積電 2330.TW 完整交易復盤");
  await expect(dialog).toContainText("1 / 2");
  const pageScrollBeforeSwitch = await page.evaluate(() => window.scrollY);
  await dialog.getByRole("button", { name: "下一筆完整交易" }).click();

  await expect(dialog).toContainText("聯發科 2454.TW 完整交易復盤");
  await expect(dialog).toContainText("2 / 2");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(pageScrollBeforeSwitch);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect.poll(() => page.locator("#root").evaluate((element) => element.hasAttribute("inert"))).toBe(false);
  await expect(
    page.locator('[data-closed-position-group="closed-mediatek-e2e"]').getByRole("button", { name: "查看完整復盤" }),
  ).toBeFocused();
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
  await closedPosition.getByRole("button", { name: "查看這次處分" }).click();

  const exitReview = page.locator('[aria-label="台積電 2330.TW 個別批次復盤"]');
  await expect(exitReview).toContainText("trade-review-v3");
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
  await closedPosition.getByRole("button", { name: "查看這次處分" }).click();

  const exitReview = page.locator('[aria-label="台積電 2330.TW 個別批次復盤"]');
  await expect(exitReview).toContainText("trade-review-v3");
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
  await closedPosition.getByRole("button", { name: "查看這次處分" }).click();

  const exitReview = page.locator('[aria-label="台積電 2330.TW 個別批次復盤"]');
  await expect(exitReview).toContainText("trade-review-v3");
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
  await closedPosition.getByRole("button", { name: "查看這次處分" }).click();

  const exitReview = page.locator('[aria-label="台積電 2330.TW 個別批次復盤"]');
  await expect(exitReview).toContainText("trade-review-v3");
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
  await closedPosition.getByRole("button", { name: "查看這次處分" }).click();

  const exitReview = page.locator('[aria-label="台積電 2330.TW 個別批次復盤"]');
  await expect(exitReview).toContainText("trade-review-v4");
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

test("Daily Radar localizes background data gaps without exposing internal reason codes", async ({ page }) => {
  const internalReason = "context_cache_missing";
  const runWithGap = {
    ...radarRun,
    candidates: [
      {
        ...radarRun.candidates[0],
        background_context_labels: [
          {
            context_type: "lending",
            label: "借券資料尚未準備完成",
            source: {},
            as_of_date: null,
            freshness: "missing",
            missing_reason: internalReason,
            replay_key: "lending:2330.TW:2026-07-16",
            applicable_consumers: ["daily_radar"],
          },
        ],
      },
    ],
  };

  await authenticate(page);
  await installApiMocks(page, { dailyRadar: runWithGap });
  await page.goto("/daily-radar");
  await page.getByRole("button", { name: "查看細節" }).click();

  const drawer = page.getByRole("dialog", { name: "台積電 · 2330.TW" });
  await expect(drawer).toContainText("缺資料原因：尚無背景資料快照");
  await expect(drawer).not.toContainText(internalReason);
});
