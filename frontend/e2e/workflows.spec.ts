import { expect, test } from "@playwright/test";
import {
  activeEtfDaily,
  authenticate,
  installApiMocks,
  quickAnalyzeResult,
  radarRun,
} from "./fixtures";

test("Active ETF tracking filters funds, shows consensus, and restores drawer focus", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  await expect(page.getByText("4 / 5", { exact: true })).toBeVisible();
  await expect(page.getByText("有資料缺口", { exact: true })).toBeVisible();
  await expect(
    page.getByText("MoneyDJ 有資料就顯示；累積前後兩個資料日後，即可計算持股變化。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "查看 2330.TW 台積電 持股變化" })).toHaveCount(2);
  await expect(page.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);

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
  await expect(drawer.getByText("比較與資料來源", { exact: true })).toBeVisible();
  const currentEvidence = drawer.getByRole("region", { name: "本期證據 2026-08-28" });
  const previousEvidence = drawer.getByRole("region", { name: "前期證據 2026-08-27" });
  await expect(currentEvidence).toContainText("MoneyDJ");
  await expect(previousEvidence).toContainText("MoneyDJ");
  await expect(currentEvidence.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
  await expect(previousEvidence.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "關閉持股變化明細" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(openDrawerButton).toBeFocused();

  await page.getByRole("button", { name: /00982A/ }).click();
  await expect(
    page.getByText("已取得 2026-08-28 MoneyDJ 持股，可比較 2026-08-27 → 2026-08-28", { exact: true }),
  ).toBeVisible();
  const openSingleSourceDrawerButton = page.getByRole("button", { name: "查看 2881.TW 富邦金 持股變化" });
  await expect(openSingleSourceDrawerButton).toBeVisible();
  await openSingleSourceDrawerButton.click();
  const singleSourceDrawer = page.getByRole("dialog", { name: "2881.TW 富邦金" });
  await expect(singleSourceDrawer.getByText("比較與資料來源", { exact: true })).toBeVisible();
  await expect(singleSourceDrawer.getByRole("region", { name: "本期證據 2026-08-28" })).toContainText("MoneyDJ");
  await expect(singleSourceDrawer.getByRole("region", { name: "前期證據 2026-08-27" })).toContainText("MoneyDJ");
  await expect(singleSourceDrawer.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /00983A/ }).click();
  await expect(page.getByText("已取得 2026-08-28 MoneyDJ 持股，尚無前期資料可比較", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "個股共識" }).click();
  await expect(page.getByText("共同增加", { exact: true })).toBeVisible();
  await expect(page.getByText("2 檔共識", { exact: true })).toBeVisible();
  await expect(page.getByText("單一基金增加", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("1 檔基金", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "增加 3" }).click();
  await expect(page.getByRole("button", { name: "增加 3" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("2330.TW", { exact: true })).toBeVisible();
  await expect(page.getByText("2317.TW", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "減少 1" }).click();
  await expect(page.getByRole("button", { name: "減少 1" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("2317.TW", { exact: true })).toBeVisible();
  await expect(page.getByText("2330.TW", { exact: true })).toHaveCount(0);
});

test("Active ETF consensus search persists across dates and combines with direction", async ({ page }, testInfo) => {
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });
  await page.route("**/active-etf-holdings/daily?*", async (route) => {
    const date = new URL(route.request().url()).searchParams.get("data_date");
    await route.fulfill({
      json: {
        ...activeEtfDaily,
        data_date: date,
        consensus: date === "2026-08-27" ? [] : activeEtfDaily.consensus,
      },
    });
  });
  await page.goto("/active-etf");
  await page.getByRole("button", { name: "個股共識" }).click();
  const search = page.getByRole("searchbox", { name: "搜尋個股" });
  await search.fill(" 2330.tw ");
  await expect(page.getByRole("button", { name: "全部 1", exact: true })).toBeVisible();
  await expect(page.getByText("2330.TW", { exact: true })).toBeVisible();
  await expect(page.getByText("2317.TW", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "減少 0", exact: true }).click();
  await expect(page.getByRole("heading", { name: "找不到符合篩選條件的個股" })).toBeVisible();
  await page.getByRole("button", { name: "全部 1", exact: true }).click();
  await page.getByRole("combobox", { name: "選擇 ETF 持股資料日" }).selectOption("2026-08-27");
  await expect(search).toHaveValue(" 2330.tw ");
  await expect(page.getByRole("button", { name: "全部 0", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "選擇 ETF 持股資料日" }).selectOption("2026-08-28");
  await expect(search).toHaveValue(" 2330.tw ");
  await expect(page.getByText("2330.TW", { exact: true })).toBeVisible();
  await search.fill("聯發科");
  await expect(page.getByText("2454.TW", { exact: true })).toBeVisible();
  await search.fill("");
  await expect(
    page.getByRole("button", { name: `全部 ${activeEtfDaily.consensus.length}`, exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(search).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("active-etf-search-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: testInfo.outputPath("active-etf-search-desktop.png"), fullPage: true });
});

test("Active ETF tracking distinguishes unchanged holdings from an unavailable source date", async ({ page }) => {
  const unchangedFund = activeEtfDaily.funds.find((fund) => fund.fund_code === "00982A");
  if (!unchangedFund) throw new Error("00982A fixture is required");
  const unchangedDaily = {
    ...activeEtfDaily,
    expected_funds: 1,
    covered_funds: 1,
    summary: {
      changed_funds: 0,
      changed_stocks: 0,
      changed_rows: 0,
      additions: 0,
      increases: 0,
      decreases: 0,
      removals: 0,
    },
    funds: [{ ...unchangedFund, change_count: 0 }],
    changes: [],
    consensus: [],
  };

  await page.setViewportSize({ width: 1280, height: 900 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily: unchangedDaily });

  await page.goto("/active-etf");
  await page.getByRole("button", { name: /00982A/ }).click();
  await expect(page.getByText("已更新・無持股變化", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("00982A 已更新，沒有持股變化", { exact: true })).toBeVisible();
  await expect(page.getByText("沒有差異也可能代表基金持股尚未更新", { exact: false })).toHaveCount(0);

  await installApiMocks(page, { activeEtfDaily });
  await page.reload();
  await page.getByRole("button", { name: /00982A/ }).click();
  await page.getByRole("button", { name: "新增持股 0" }).click();
  await expect(page.getByRole("heading", { name: "目前篩選條件沒有持股變化" })).toBeVisible();
  await expect(page.getByText("這不是零變化紀錄。", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: /00409A/ }).click();
  await expect(page.getByText("來源尚未提供", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "MoneyDJ 尚未提供 2026-08-28 持股" })).toBeVisible();
  await expect(page.getByText("00409A 最新資料日 2026-08-27；這不是零變化紀錄。", { exact: true })).toBeVisible();
});

test("Active ETF consensus opens contributing fund details and restores focus", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  await page.getByRole("button", { name: "個股共識" }).click();

  const openDrawerButton = page.getByRole("button", { name: "查看 2330.TW 台積電 的 2 檔 ETF 變化" });
  await openDrawerButton.click();

  const drawer = page.getByRole("dialog", { name: "2330.TW 台積電" });
  await expect(drawer.getByText("00985A", { exact: true })).toBeVisible();
  await expect(drawer.getByText("00980A", { exact: true })).toBeVisible();
  await expect(drawer.getByText("新增持股", { exact: true })).toHaveCount(2);
  await expect(drawer.getByText("+800,000", { exact: true })).toBeVisible();
  await expect(drawer.getByText("+320,000", { exact: true })).toBeVisible();
  await expect(drawer.getByText("雙來源確認", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "關閉個股 ETF 變化明細" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(openDrawerButton).toBeFocused();
});

test("Active ETF consensus preserves each fund comparison period", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, {
    activeEtfDaily: {
      ...activeEtfDaily,
      funds: activeEtfDaily.funds.map((fund) =>
        fund.fund_code === "00980A" ? { ...fund, previous_date: "2026-08-26" } : fund,
      ),
      changes: activeEtfDaily.changes.map((change) =>
        change.fund_code === "00980A" && change.symbol === "2330.TW"
          ? { ...change, previous_date: "2026-08-26" }
          : change,
      ),
    },
  });

  await page.goto("/active-etf");
  await page.getByRole("button", { name: "個股共識" }).click();
  await page.getByRole("button", { name: "查看 2330.TW 台積電 的 2 檔 ETF 變化" }).click();

  const drawer = page.getByRole("dialog", { name: "2330.TW 台積電" });
  await expect(drawer.getByText("比較區間依基金而異 · 2 檔基金", { exact: true })).toBeVisible();
  await expect(drawer.getByText("2026-08-27 → 2026-08-28", { exact: true })).toBeVisible();
  await expect(drawer.getByText("2026-08-26 → 2026-08-28", { exact: true })).toBeVisible();
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

test("Active ETF tracking keeps MoneyDJ status readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/active-etf");
  await expect(page.getByText("4 / 5", { exact: true })).toBeVisible();
  await expect(
    page.getByText("MoneyDJ 有資料就顯示；累積前後兩個資料日後，即可計算持股變化。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("雙來源確認", { exact: true })).toHaveCount(0);

  await page.getByRole("combobox", { name: "基金" }).selectOption("00985A");
  await expect(page.getByRole("button", { name: "查看全部基金" })).toBeVisible();
  expect(await page.locator("body").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

  await page.getByRole("button", { name: "查看 2454.TW 聯發科 持股變化" }).click();
  const mobileDrawer = page.getByRole("dialog", { name: "2454.TW 聯發科" });
  await expect(mobileDrawer.getByRole("region", { name: "本期證據 2026-08-28" })).toBeAttached();
  await expect(mobileDrawer.getByRole("region", { name: "前期證據 2026-08-27" })).toBeAttached();
  expect(await mobileDrawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");

  await page.getByRole("combobox", { name: "基金" }).selectOption("00409A");
  await expect(page.getByRole("heading", { name: "MoneyDJ 尚未提供 2026-08-28 持股" })).toBeVisible();
  expect(await page.locator("body").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

  await page.getByRole("button", { name: "個股共識" }).click();
  await expect(page.getByText("2 檔共識", { exact: true })).toBeVisible();
  await expect(page.getByText("1 檔基金", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "增加 3" }).click();
  await expect(page.getByText("2317.TW", { exact: true })).toHaveCount(0);
  expect(await page.locator("body").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);

  const openConsensusDrawerButton = page.getByRole("button", { name: "查看 2330.TW 台積電 的 2 檔 ETF 變化" });
  await openConsensusDrawerButton.click();
  const consensusDrawer = page.getByRole("dialog", { name: "2330.TW 台積電" });
  await expect(consensusDrawer.getByText("00985A", { exact: true })).toBeVisible();
  await expect(consensusDrawer.getByText("00980A", { exact: true })).toBeVisible();
  expect(await consensusDrawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 320, height: 700 });
  expect(await consensusDrawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(openConsensusDrawerButton).toBeFocused();
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

  await page.getByRole("button", { name: "增加 2" }).click();
  await expect(page.getByText("2330.TW", { exact: true })).toHaveCount(0);
  await expect(page.getByText("2454.TW", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "減少 1" }).click();
  await expect(page.getByText("2317.TW", { exact: true })).toBeVisible();
  await expect(page.getByText("2330.TW", { exact: true })).toHaveCount(0);
});

test("Active ETF tracking normalizes the previous verified-only API contract", async ({ page }) => {
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
  await expect(page.getByText("雙來源確認", { exact: true })).toHaveCount(0);
  await expect(page.getByText("發行投信官方資料", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "查看 2454.TW 聯發科 持股變化" }).click();
  const drawer = page.getByRole("dialog", { name: "2454.TW 聯發科" });
  await expect(
    drawer.getByText("此 API 版本未提供前後期來源明細，無法顯示各期的原始資料連結。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(drawer.getByText("MoneyDJ", { exact: true })).toHaveCount(1);
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

test("Analyze deterministic research supports copy without retired feature requests", async ({
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
  await expect(page.getByText("行情開／高／低", { exact: true })).toBeVisible();
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
    .toContain("行情開／高／低：3075 / 3155 / 3050");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("20／60 日均成交量：2,100 / 1,800");
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("現價：3120（TWSE MIS 即時）（漲停）");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("MA20 5日斜率：+1.234%");
  await expect(page.getByText("MACD 柱體單日增減", { exact: true })).toBeVisible();
  await expect(page.getByText("+0.167", { exact: true })).toBeVisible();
  await expect(page.getByText("+13,535,774", { exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("indicator-comparisons.png"), fullPage: true });
  const indicatorCopy = await page.evaluate(() => navigator.clipboard.readText());
  expect(indicatorCopy).toContain("MACD 柱體 3日淨變化／股價：-0.0456%");
  expect(indicatorCopy).toContain("MACD 柱體單日增減：+0.167");
  expect(indicatorCopy).toContain("MACD 三日分類資料日：2026-07-15");
  expect(indicatorCopy).toContain("OBV 起算日（首筆歸零）：2025-07-17");
  expect(indicatorCopy).toContain("OBV 單日增減（同序列）：+13,535,774");
  expect(indicatorCopy).toContain("勿跨摘要相減");
  expect(indicatorCopy).not.toContain("3日斜率");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).not.toContain("波動狀態");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).not.toContain("訊號衝突");

  await expect(page.getByRole("button", { name: /加入持股|加入關注/ })).toHaveCount(0);

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

test("Legacy indicator summaries leave unavailable comparisons unknown", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, {
    analyzeResult: {
      ...quickAnalyzeResult,
      technical_indicators: { ma5: 3080, ma20: 3010, ma60: 2860, macd_hist: 2.293, obv: 597572897 },
    },
  });
  await page.goto("/analyze");
  await page.getByRole("textbox", { name: "股票代碼" }).fill("3661.TW");
  await page.getByRole("button", { name: "開始分析" }).click();
  await page.getByRole("button", { name: "複製技術指標摘要" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toContain("OBV 單日增減（同序列）：資料不足");
  const copy = await page.evaluate(() => navigator.clipboard.readText());
  expect(copy).toContain("MACD 柱體單日增減：資料不足");
  expect(copy).toContain("OBV 起算日（首筆歸零）：資料不足");
  expect(copy).toContain("指標資料日／前一交易日：資料不足 / 資料不足");
});

test("Indicator provenance keeps historical dates and full calculation precision", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, { analyzeResult: {
    ...quickAnalyzeResult,
    is_final: false,
    snapshot: { ...quickAnalyzeResult.snapshot, market_current_price: 2460,
      market_quote_time: "2026-09-07T10:15:23+08:00", market_day_open: null,
      market_day_high: null, market_day_low: null },
    technical_indicators: { ...quickAnalyzeResult.technical_indicators,
      ma5: 2405.12, ma20: 2403.25, ma60: 2300.77, bollinger_mid: 2403.25, bollinger_upper: 2452.06,
      indicator_data_date: "2026-09-04", donchian_position: "upper_half", donchian_upper: 2445, donchian_lower: 2300,
      input_context: { indicator_mode: "completed_daily", indicator_close_confirmed: true,
        breakout_close_confirmed: false, history_completed_through: "2026-09-04",
        breakout_reference_price: 2440, indicator_close: 2440, hlc_status: "complete" } },
    phase1_observation: { data_date: "2026-09-04", symbol: "3661.TW", dataset: "daily",
      adjustment_mode: "adjusted", freshness: "fresh", missing_reason: null,
      source: { provider: "test", dataset: "daily", adjustment_mode: "adjusted" },
      source_granularity: "daily", data_quality: {}, anchors: {
      swing_low_60d: { available: true, avwap: 2450, current_distance_to_avwap_pct: 0.50 },
    } },
  } });
  await page.goto("/analyze");
  await page.getByRole("textbox", { name: "股票代碼" }).fill("3661.TW");
  await page.getByRole("button", { name: "開始分析" }).click();
  await page.getByRole("button", { name: "複製技術指標摘要" }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("行情時間：2026-09-07 10:15:23 +08:00");
  const copy = await page.evaluate(() => navigator.clipboard.readText());
  expect(copy).toContain("行情開／高／低：—");
  expect(copy).toContain("指標所屬交易日：2026-09-04");
  expect(copy).toContain("不同交易日；日線指標未以所列現價重算");
  expect(copy).toContain("2405.12 / 2403.25 / 2300.77");
  expect(copy).toContain("2450.00 / 距離 +0.41%");
  expect(copy).toContain("布林通道位階：高於上軌");
  expect(copy).toContain("高於唐奇安上緣");
  expect(copy).toContain("已收盤確認");
  expect(copy).not.toContain("盤中單日增減尚未定案");
  expect(copy).toContain("行情狀態：盤中快照");
  expect(copy).toContain("指標狀態：完整日線收盤");
});

for (const [price, position] of [[2500, "高於唐奇安上緣"], [2460, "觸及唐奇安上緣，尚未突破"], [2400, "現價位於唐奇安通道內"], [2300, "觸及唐奇安下緣，尚未跌破"], [2200, "低於唐奇安下緣"]] as const) {
  test(`Undated quote ${price} separates Donchian position from event`, async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await authenticate(page);
    await installApiMocks(page, { analyzeResult: {
      ...quickAnalyzeResult, is_final: false,
      snapshot: { ...quickAnalyzeResult.snapshot, market_current_price: price, market_quote_time: null, market_trade_date: null },
      technical_indicators: { ...quickAnalyzeResult.technical_indicators,
        indicator_data_date: "2026-09-07", macd_hist_change_1d: 3.223,
        donchian_upper: 2460, donchian_lower: 2300,
        macd_trend_hist: 3.247, macd_hist_3d_previous: 1.5, macd_hist_change_3d: 1.747,
        macd_trend_price: 2440, macd_trend_previous_date: "2026-09-02",
        obv_window_previous_date: "2026-08-31", obv_window_previous_close: 2400,
        obv_window_close: 2440, obv_window_previous: 1000, obv_window_change: 500,
        obv_window_price_change_pct: 1.6666667,
        input_context: { indicator_mode: "completed_daily", indicator_close_confirmed: true,
          breakout_baseline_through: "2026-09-07", obv_lookback: 5 } },
    } });
    await page.goto("/analyze");
    await page.getByRole("textbox", { name: "股票代碼" }).fill("3661.TW");
    await page.getByRole("button", { name: "開始分析" }).click();
    await page.getByRole("button", { name: "複製技術指標摘要" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(`唐奇安通道位階：${position}`);
    const copy = await page.evaluate(() => navigator.clipboard.readText());
    expect(copy).toContain("突破事件：無法確認（行情交易日未知）");
    expect(copy).toContain("單日增減 +3.223 已收盤確認");
    expect(copy).toContain("MACD 三交易日前柱體（同序列）：1.500");
    expect(copy).toContain("MACD 三日淨變化：+1.747");
    expect(copy).toContain("OBV 比較起日收盤價：2400.00");
    expect(copy).toContain("OBV 比較窗淨變化：+500");
    expect(copy).toContain("(MA20[t] / MA20[t-5] - 1) × 100%");
  });
}
