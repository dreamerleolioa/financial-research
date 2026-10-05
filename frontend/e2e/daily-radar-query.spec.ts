import { expect, test } from "@playwright/test";
import { authenticate, installApiMocks, radarRun } from "./fixtures";

test("Radar separates returning membership from signal strength and shows prior dates", async ({ page }) => {
  await authenticate(page);
  const returningRun = structuredClone(radarRun);
  const candidate = returningRun.candidates[0];
  candidate.repeat_status = "upgraded";
  candidate.input_snapshot.observation_history = {
    membership_status: "returning", first_seen_date: "2026-06-03", last_seen_date: "2026-09-24",
    appearance_count: 7, consecutive_trading_days: 1, signal_status: "improved",
  };
  await installApiMocks(page, { dailyRadar: returningRun });
  await page.goto("/daily-radar");
  await expect(page.getByText("重新列入觀察", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/上次 2026-09-24/).first()).toBeVisible();
  await expect(page.getByText("觀察強度提升", { exact: true }).first()).toBeVisible();
});

test("Radar reuses fresh results when returning from another research page", async ({ page }) => {
  await authenticate(page);
  const requests: string[] = [];
  await installApiMocks(page, { dailyRadar: radarRun, requestLog: requests });
  await page.goto("/daily-radar");
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  const reads = requests.filter((request) => request === "GET /daily-radar/latest").length;
  await page.getByRole("link", { name: "個股分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "開始分析" })).toBeVisible();
  await page.getByRole("link", { name: "盤後觀察雷達", exact: true }).click();
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  expect(requests.filter((request) => request === "GET /daily-radar/latest")).toHaveLength(reads);
});

test("Radar keeps the last successful results visible if a manual refresh fails", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: radarRun });
  await page.goto("/daily-radar");
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  await page.route("**/daily-radar/latest", (route) => route.fulfill({
    status: 503, contentType: "application/json", body: JSON.stringify({ detail: "temporary failure" }),
  }));
  await page.getByRole("button", { name: "重新整理", exact: true }).click();
  await expect(page.getByText("Daily Radar 觀察資料讀取失敗，請稍後再試。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  await expect(page.getByText("更新失敗，以下保留上次成功讀取的資料。", { exact: true })).toBeVisible();
});

test("Radar distinguishes no published run from a service failure", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page);
  await page.goto("/daily-radar");
  await expect(page.getByText("Daily Radar 觀察資料讀取失敗，請稍後再試。", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "目前沒有可顯示的觀察候選" })).toBeVisible();
});

test("Radar displays cached data throughout a stale background refresh", async ({ page }) => {
  await page.clock.install();
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: radarRun });
  await page.goto("/daily-radar");
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  await page.getByRole("link", { name: "個股分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "開始分析" })).toBeVisible();
  await page.clock.fastForward(61_000);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/daily-radar/latest", async (route) => {
    await pending;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(radarRun) });
  });
  await page.getByRole("link", { name: "盤後觀察雷達", exact: true }).click();
  try {
    await expect(page.getByRole("button", { name: "讀取中…", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
    await expect(page.getByText("資料載入中", { exact: true })).toHaveCount(0);
  } finally {
    release();
  }
  await expect(page.getByRole("button", { name: "重新整理", exact: true })).toBeEnabled();
});

test("Radar clears an old result when the server reports no public run", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: radarRun });
  await page.goto("/daily-radar");
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toBeVisible();
  await page.route("**/daily-radar/latest", (route) => route.fulfill({
    status: 404, contentType: "application/json",
    body: JSON.stringify({ detail: "No public Daily Radar run is available." }),
  }));
  await page.getByRole("button", { name: "重新整理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "目前沒有可顯示的觀察候選" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "候選觀察清單" })).toHaveCount(0);
});
