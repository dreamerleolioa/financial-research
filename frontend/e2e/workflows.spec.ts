import { expect, test } from "@playwright/test";
import {
  authenticate,
  closedPortfolioItem,
  installApiMocks,
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
  await page.getByRole("button", { name: "複製技術指標摘要" }).click();
  await expect(page.getByRole("button", { name: "複製技術指標摘要" })).toContainText("已複製");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("3661.TW");

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

test("Watchlist quick lookup preserves the copy-to-AI workflow", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await authenticate(page);
  await installApiMocks(page, {
    watchlist: [watchlistItem],
    analyzeResult: quickAnalyzeResult,
  });

  await page.goto("/watchlist");
  await page.getByRole("button", { name: "技術快查" }).click();
  const copyButton = page.getByRole("button", { name: "複製 世芯-KY 3661.TW 技術指標" });
  await expect(copyButton).toBeVisible();
  await copyButton.click();
  await expect(copyButton).toContainText("已複製");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("3661.TW");
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
