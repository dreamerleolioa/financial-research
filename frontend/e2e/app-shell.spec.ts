import { expect, test } from "@playwright/test";
import { authenticate, installApiMocks } from "./fixtures";

test("desktop shell exposes primary routes and portfolio subviews", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await authenticate(page);
  await installApiMocks(page);

  await page.goto("/analyze");
  await expect(page.getByRole("navigation", { name: "主要功能", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "行動版主要功能", exact: true })).toBeHidden();

  await page.getByRole("link", { name: "關注列表" }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
  await expect(page.getByRole("heading", { name: "建立第一筆觀察標的" })).toBeVisible();

  await page.getByRole("link", { name: "持股管理" }).first().click();
  await expect(page).toHaveURL(/\/portfolio$/);
  await page.getByRole("link", { name: "已結案" }).first().click();
  await expect(page).toHaveURL(/\/portfolio\/closed$/);
  await expect(page.getByRole("heading", { name: "此期間沒有結案紀錄" })).toBeVisible();
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

  await page.getByRole("link", { name: "持股" }).click();
  await expect(page).toHaveURL(/\/portfolio$/);
  await page.getByRole("link", { name: "已結案" }).click();
  await expect(page).toHaveURL(/\/portfolio\/closed$/);

  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

for (const width of [1280, 1024, 375, 320]) {
  test(`core routes do not overflow horizontally at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await authenticate(page);
    await installApiMocks(page);

    for (const pathname of ["/analyze", "/watchlist", "/portfolio", "/portfolio/closed", "/daily-radar"]) {
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
