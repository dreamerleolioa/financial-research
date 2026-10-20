import { expect, test } from "@playwright/test";
import { activeEtfDaily, authenticate, installApiMocks } from "./fixtures";

const rangeData = {
  start_date: "2026-07-30",
  end_date: "2026-08-28",
  available_dates: ["2026-08-28", "2026-08-27", "2026-08-26"],
  observed_dates: ["2026-08-26", "2026-08-27", "2026-08-28"],
  funds: [
    {
      fund_code: "00985A",
      fund_name: "主動野村台灣50",
      first_date: "2026-08-26",
      last_date: "2026-08-28",
      snapshot_count: 3,
      missing_dates: [],
    },
  ],
  stocks: [
    {
      symbol: "2330.TW",
      name: "台積電",
      funds: [
        {
          fund_code: "00985A",
          fund_name: "主動野村台灣50",
          first_date: "2026-08-26",
          last_date: "2026-08-28",
          first_shares: 100,
          last_shares: 100,
          net_share_delta: 0,
          first_weight_pct: "10.0000",
          last_weight_pct: "11.0000",
          weight_delta_pct_points: "1.0000",
          increase_days: 1,
          decrease_days: 1,
          added_days: 0,
          removed_days: 0,
          scale_change_days: 0,
        },
      ],
    },
  ],
  timeline: [26, 27, 28].map((day, index) => ({
    fund_code: "00985A",
    data_date: `2026-08-${day}`,
    previous_date: index ? `2026-08-${day - 1}` : null,
    shares: [100, 150, 100][index],
    weight_pct: "11.0000",
    share_delta: [null, 50, -50][index],
    action: [null, "increased", "decreased"][index],
    likely_fund_scale_change: false,
    source_url: "https://www.moneydj.com/ETF/",
    fetched_at: "2026-08-28T06:00:00Z",
  })),
};

for (const mode of ["range", "daily"] as const) {
  test(`ETF ${mode} search preserves Zhuyin composition until text is committed`, async ({ page }, testInfo) => {
    await authenticate(page);
    await installApiMocks(page, { activeEtfDaily });
    await page.route("**/active-etf-holdings/range**", (route) => route.fulfill({ json: rangeData }));
    await page.goto(`/active-etf?mode=${mode}&view=consensus&date=2026-08-28&q=2330`);
    const search = page.getByRole("searchbox", { name: "搜尋個股" });
    await expect(search).toHaveValue("2330");
    await search.fill("");
    await expect(page).not.toHaveURL(/[?&]q=/);
    await search.focus();

    // Chromium's IME protocol exercises the browser's composition lifecycle;
    // fill("台積電") alone never enters the preedit state reported by the user.
    const ime = await page.context().newCDPSession(page);
    await ime.send("Input.imeSetComposition", { text: "ㄊ", selectionStart: 1, selectionEnd: 1 });
    await expect(search).toHaveValue("ㄊ");
    await ime.send("Input.imeSetComposition", { text: "ㄊㄞˊ", selectionStart: 3, selectionEnd: 3 });
    await expect(search).toHaveValue("ㄊㄞˊ");
    await expect(page).not.toHaveURL(/[?&]q=/);
    await page.screenshot({ path: testInfo.outputPath(`${mode}-zhuyin-preedit.png`) });
    await expect(
      page.getByRole("button", {
        name: mode === "range" ? "查看 2330.TW 台積電 區間明細" : "查看 2330.TW 台積電 的 2 檔 ETF 變化",
      }),
    ).toBeVisible();

    await ime.send("Input.insertText", { text: "台" });
    await expect(search).toHaveValue("台");
    await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBe("台");
    await ime.send("Input.imeSetComposition", { text: "ㄐㄧ", selectionStart: 2, selectionEnd: 2 });
    await expect(search).toHaveValue("台ㄐㄧ");
    await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBe("台");
    await ime.send("Input.insertText", { text: "積電" });
    await expect(search).toHaveValue("台積電");
    await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBe("台積電");
    await page.screenshot({ path: testInfo.outputPath(`${mode}-zhuyin-committed.png`) });

    await search.press("Backspace");
    await expect(search).toHaveValue("台積");
    await search.press("ControlOrMeta+A");
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.evaluate(() => navigator.clipboard.writeText("台積電"));
    await search.press("ControlOrMeta+V");
    await expect(search).toHaveValue("台積電");
    await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBe("台積電");
    await search.fill("2330.TW");
    await search.pressSequentially("1234567890");
    await expect(search).toHaveValue("2330.TW1234567890");
    await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBe("2330.TW1234567890");
    await search.fill("");
    await expect(page).not.toHaveURL(/[?&]q=/);
    await ime.send("Input.imeSetComposition", { text: "ㄓ", selectionStart: 1, selectionEnd: 1 });
    await ime.send("Input.imeSetComposition", { text: "", selectionStart: 0, selectionEnd: 0 });
    await expect(search).toHaveValue("");
    await expect(page).not.toHaveURL(/[?&]q=/);
    await ime.detach();

    if (mode === "range") {
      await search.fill("台積電");
      await page.getByLabel("開始日期").fill("2026-08-26");
      await page.getByRole("button", { name: "套用區間" }).click();
      await expect(page.getByLabel("開始日期")).toHaveValue("2026-08-26");
      await search.fill("2330");
      await page.goBack();
      await expect(search).toHaveValue("台積電");
      await page.goForward();
      await expect(search).toHaveValue("2330");
    }
  });
}

test("ETF interval keeps shared stock search, independent dates, and drilldown return", async ({ page }, testInfo) => {
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });
  await page.route("**/active-etf-holdings/range**", async (route) => {
    const query = new URL(route.request().url()).searchParams;
    await route.fulfill({
      json: {
        ...rangeData,
        start_date: query.get("start_date") ?? rangeData.start_date,
        end_date: query.get("end_date") ?? rangeData.end_date,
        timeline: query.get("symbol") ? rangeData.timeline : [],
      },
    });
  });
  await page.goto("/active-etf?date=2026-08-28");
  await page.getByRole("button", { name: "個股共識", exact: true }).click();
  const search = page.getByRole("searchbox", { name: "搜尋個股" });
  await search.fill("2330");
  await page.getByRole("button", { name: "區間觀察", exact: true }).click();
  await expect(search).toHaveValue("2330");
  await expect(page.getByLabel("開始日期")).toHaveValue("2026-07-30");
  await page.getByRole("button", { name: "近一週", exact: true }).click();
  await expect(page.getByLabel("開始日期")).toHaveValue("2026-08-22");
  await page.getByRole("button", { name: "單日觀察", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "選擇 ETF 持股資料日" })).toHaveValue("2026-08-28");
  await expect(search).toHaveValue("2330");
  await page.getByRole("button", { name: "區間觀察", exact: true }).click();
  await expect(page.getByLabel("開始日期")).toHaveValue("2026-08-22");
  await page.getByRole("button", { name: "查看 2330.TW 台積電 區間明細" }).click();
  const detail = page.getByRole("dialog", { name: "2330.TW 台積電" });
  await expect(detail.getByRole("button", { name: "關閉區間持股明細" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(detail).toBeHidden();
  const openDetail = page.getByRole("button", { name: "查看 2330.TW 台積電 區間明細" });
  await expect(openDetail).toBeFocused();
  const scrollY = await page.evaluate(() => window.scrollY);
  await openDetail.click();
  await expect(detail).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollY);
  await expect(detail).toContainText("增加 1 天 · 減少 1 天");
  await expect(detail).toContainText("實際比較 2026-08-26 → 2026-08-28");
  await expect(detail.getByRole("cell", { name: "+50", exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "查看 2026-08-28 00985A 單日觀察" }).click();
  await expect(search).toHaveValue("2330.TW");
  await page.goBack();
  await expect(detail).toBeVisible();
  await expect(search).toHaveValue("2330");
  await page.goForward();
  await expect(search).toHaveValue("2330.TW");
  await page.getByRole("button", { name: "返回區間觀察", exact: true }).click();
  await expect(search).toHaveValue("2330");
  await expect(page.getByLabel("開始日期")).toHaveValue("2026-08-22");
  await expect(detail).toBeVisible();
  await page.reload();
  await expect(search).toHaveValue("2330");
  await expect(detail).toBeVisible();
  await detail.getByRole("button", { name: "查看 2026-08-28 00985A 單日觀察" }).click();
  await page.getByRole("button", { name: "區間觀察", exact: true }).click();
  await expect(search).toHaveValue("2330");
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await page.getByLabel("開始日期").boundingBox())!.width).toBeGreaterThan(120);
  expect((await page.getByLabel("結束日期").boundingBox())!.width).toBeGreaterThan(120);
  await expect(detail).toBeVisible();
  expect((await detail.boundingBox())!.width).toBeLessThanOrEqual(375);
  await page.screenshot({ path: testInfo.outputPath("range-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: testInfo.outputPath("range-desktop.png"), fullPage: true });
  await detail.getByRole("button", { name: "關閉區間持股明細" }).click();
  await expect(detail).toBeHidden();
  await search.fill("不存在");
  await expect(page.getByRole("heading", { name: "找不到符合篩選條件的個股" })).toBeVisible();
  await search.fill("");
  await expect(page.getByRole("button", { name: "查看 2330.TW 台積電 區間明細" })).toBeVisible();
  await page.getByLabel("開始日期").fill("2026-09-01");
  await expect(page.getByRole("button", { name: "套用區間" })).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("開始日期不可晚於結束日期");
});

test("ETF interval distinguishes an empty date window and malformed data", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });
  await page.route("**/active-etf-holdings/range**", (route) =>
    route.fulfill({
      json: {
        ...rangeData,
        observed_dates: [],
        stocks: [],
        timeline: [],
        funds: [{ ...rangeData.funds[0], first_date: null, last_date: null, snapshot_count: 0 }],
      },
    }),
  );
  await page.goto("/active-etf?mode=range");
  await expect(page.getByRole("heading", { name: "這段期間尚無持股快照" })).toBeVisible();
  await page.route("**/active-etf-holdings/range**", (route) =>
    route.fulfill({
      json: {
        ...rangeData,
        stocks: [{ ...rangeData.stocks[0], funds: [{ ...rangeData.stocks[0].funds[0], first_weight_pct: "invalid" }] }],
      },
    }),
  );
  await page.reload();
  await expect(page.getByRole("heading", { name: "區間持股暫時無法載入" })).toBeVisible();
});
