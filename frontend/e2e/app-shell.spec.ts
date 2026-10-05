import { expect, test } from "@playwright/test";
import { activeEtfDaily, authenticate, installApiMocks } from "./fixtures";

test("desktop shell exposes the three research routes", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await authenticate(page);
  await installApiMocks(page, { activeEtfDaily });

  await page.goto("/analyze");
  await expect(page.getByRole("navigation", { name: "主要功能", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "行動版主要功能", exact: true })).toBeHidden();

  await page.getByRole("link", { name: "主動式 ETF" }).click();
  await expect(page).toHaveURL(/\/active-etf$/);
  await expect(page.getByRole("heading", { name: "主動式 ETF 持股追蹤", level: 2 })).toBeVisible();
});

test("mobile shell uses bottom navigation and keeps the selected theme", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await authenticate(page, "light");
  await installApiMocks(page);

  await page.goto("/analyze");
  await expect(page.getByRole("navigation", { name: "行動版主要功能", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主要功能", exact: true })).toBeHidden();

  await page.getByRole("button", { name: "切換為暗色模式" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);

  await page.getByRole("link", { name: "雷達" }).click();
  await expect(page).toHaveURL(/\/daily-radar$/);

  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

for (const width of [1280, 1024, 375, 320]) {
  test(`core routes do not overflow horizontally at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await authenticate(page);
    await installApiMocks(page, { activeEtfDaily });

    for (const pathname of [
      "/analyze",
      "/daily-radar",
      "/active-etf",
    ]) {
      await page.goto(pathname);
      await expect(page.locator("main")).toBeVisible();
      const dimensions = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      expect(dimensions.scrollWidth, `${pathname} overflowed at ${width}px`).toBe(dimensions.clientWidth);
    }
  });
}

for (const pathname of ["/watchlist", "/portfolio", "/portfolio/closed"]) {
  test(`retired route ${pathname} returns to research without obsolete API requests`, async ({ page }) => {
    await authenticate(page);
    const requests: string[] = [];
    await installApiMocks(page, { requestLog: requests });
    await page.goto(pathname);
    await expect(page).toHaveURL(/\/analyze$/);
    await expect(page.getByRole("button", { name: "開始分析" })).toBeVisible();
    await expect(page.getByRole("link", { name: /關注列表|持股管理|已結案/ })).toHaveCount(0);
    expect(requests.filter((request) => /\/(portfolio|watchlist)/.test(request))).toEqual([]);
  });
}
