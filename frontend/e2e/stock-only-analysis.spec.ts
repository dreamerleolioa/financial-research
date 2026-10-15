import { expect, test } from "@playwright/test";
import { authenticate, installApiMocks } from "./fixtures";

test("ETF analysis is rejected locally and stock analysis remains usable", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page);
  const analyzedSymbols: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/analyze" && request.method() === "POST") {
      analyzedSymbols.push(request.postDataJSON().symbol);
    }
  });
  await page.goto("/analyze");
  const symbol = page.getByLabel("股票代碼");
  await symbol.fill("0050.TW");
  await symbol.press("Enter");
  await expect(page.getByText("目前僅支援台灣上市與上櫃個股，不支援 ETF、權證或特別股分析。", { exact: true })).toBeVisible();
  expect(analyzedSymbols).toEqual([]);
  await symbol.fill(" 2330.tw ");
  await symbol.press("Enter");
  await expect.poll(() => analyzedSymbols).toEqual(["2330.TW"]);
  await expect(page.getByRole("link", { name: /ETF/ }).first()).toBeVisible();
});
