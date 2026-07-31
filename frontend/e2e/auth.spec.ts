import { expect, test } from "@playwright/test";
import { installApiMocks } from "./fixtures";

test("protected routes redirect to the branded login screen and persist theme choice", async ({ page }) => {
  await installApiMocks(page);

  await page.goto("/analyze");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "回到你的個股研究工作區" })).toBeVisible();
  await expect(page.getByRole("button", { name: "使用 Google 帳號登入" })).toBeVisible();

  await page.getByRole("button", { name: "切換為暗色模式" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByRole("button", { name: "切換為亮色模式" })).toBeVisible();
});

test("callback without an authorization code explains recovery", async ({ page }) => {
  await installApiMocks(page);

  await page.goto("/login/callback");
  await expect(page.getByRole("heading", { name: "登入尚未完成" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("未收到 Google 授權碼");

  await page.getByRole("link", { name: "返回登入頁" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("auth state and query cache follow token changes from another tab", async ({ context, page }) => {
  const alice = {
    id: 1,
    email: "alice@example.com",
    name: "Alice",
    avatar_url: null,
  };
  const bob = {
    id: 2,
    email: "bob@example.com",
    name: "Bob",
    avatar_url: null,
  };
  const usersByToken = {
    "alice-token": alice,
    "bob-token": bob,
  };

  await page.addInitScript(() => localStorage.setItem("auth_token", "alice-token"));
  await installApiMocks(page, { usersByToken });
  await page.goto("/analyze");
  await expect(page.getByText("alice@example.com", { exact: true })).toBeVisible();

  const accountSwitcher = await context.newPage();
  await installApiMocks(accountSwitcher, { usersByToken });
  await accountSwitcher.goto("/login");
  await accountSwitcher.evaluate(() => localStorage.setItem("auth_token", "bob-token"));

  await expect(page.getByText("bob@example.com", { exact: true })).toBeVisible();
  await expect(page.getByText("alice@example.com", { exact: true })).toHaveCount(0);
  await accountSwitcher.close();
});
