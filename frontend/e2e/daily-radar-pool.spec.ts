import { expect, test } from "@playwright/test";
import { authenticate, installApiMocks, radarRun } from "./fixtures";

test("Pool summary discloses scan scope and separates data gaps from selection", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: {
    ...radarRun,
    pool_summary: {
      version: "candidate-pool-summary-v1", population_scope: "scored_raw_records",
      input_record_count: 8,
      state_counts: { selected: 2, data_pending: 2, eligibility_excluded: 1,
        signal_filtered: 1, limit_deferred: 1, processing_error: 0 },
      duplicate_record_count: 0, unclassified_record_count: 1,
      comparable_shadow_count: 2, eligibility_audit_shadow_count: 1,
      reason_counts: { data_gap: 2, low_liquidity: 1 },
    },
  } });
  await page.goto("/daily-radar");
  const panel = page.getByRole("heading", { name: "候選池篩選摘要" }).locator("..");
  await expect(panel).toContainText("非全市場涵蓋率");
  await expect(panel.getByText("資料待確認", { exact: true }).locator("..")).toContainText("2");
  await expect(panel).toContainText("未分類資料列 1 筆");
  await panel.getByText("查看篩選原因", { exact: true }).click();
  await expect(panel).toContainText("必要資料缺漏：2 檔");
  await page.getByRole("tab", { name: "支撐回測", exact: false }).click();
  await expect(panel.getByText("已入池", { exact: true }).locator("..")).toContainText("2");
});
