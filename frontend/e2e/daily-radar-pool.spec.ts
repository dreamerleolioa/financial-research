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

test("Research-state filtering retains waiting candidates and original ranking", async ({ page }) => {
  await authenticate(page);
  const base = radarRun.candidates[0];
  await installApiMocks(page, { dailyRadar: {
    ...radarRun, candidates: [
      { ...base, research_status: "waiting_for_consolidation", observation_score: 90 },
      { ...base, symbol: "2317.TW", name: "鴻海", research_status: "trend_forming", observation_score: 85 },
      { ...base, symbol: "1234.TW", name: "資料未齊", research_status: "data_pending", observation_score: 80 },
    ],
  } });
  await page.goto("/daily-radar");
  await expect(page.locator("[data-daily-radar-candidate]")).toHaveCount(3);
  await expect(page.locator("[data-daily-radar-candidate]").first()).toHaveAttribute("data-daily-radar-candidate", "2330.TW");
  await page.getByLabel("研究狀態", { exact: true }).selectOption("waiting_for_consolidation");
  await expect(page.locator("[data-daily-radar-candidate]")).toHaveCount(1);
  await expect(page.locator("[data-daily-radar-candidate]")).toHaveAttribute("data-daily-radar-candidate", "2330.TW");
  await page.getByRole("button", { name: "查看細節", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("研究狀態：等待整理");
  await page.getByRole("button", { name: "關閉候選追蹤細節" }).click();
  await page.getByLabel("研究狀態", { exact: true }).selectOption("data_pending");
  await expect(page.locator("[data-daily-radar-candidate]")).toHaveAttribute("data-daily-radar-candidate", "1234.TW");
  await page.getByLabel("研究狀態", { exact: true }).selectOption("all");
  await expect(page.locator("[data-daily-radar-candidate]")).toHaveCount(3);
});

test("Research details show discovery sources and an explicitly dated score change", async ({ page }) => {
  await authenticate(page);
  const base = radarRun.candidates[0];
  await installApiMocks(page, { dailyRadar: {
    ...radarRun, candidates: [{ ...base, research_status: "trend_forming", input_snapshot: {
      universe: { institutional_universe_tracks: ["market_trend", "foreign_same_day", "unknown_private_track"] },
      observation_history: { membership_status: "returning", score_comparison: {
        status: "comparable", previous_date: "2026-07-10", previous_score: 78, score_change: 8,
      } },
    } }],
  } });
  await page.goto("/daily-radar");
  await page.getByRole("button", { name: "查看細節", exact: true }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("較前次入池（2026-07-10）分數 +8");
  await expect(drawer).toContainText("市場中期趨勢探索、外資當日買超");
  await expect(drawer).not.toContainText("unknown_private_track");
});

test("Version changes do not display a misleading score improvement", async ({ page }) => {
  await authenticate(page);
  await installApiMocks(page, { dailyRadar: {
    ...radarRun, candidates: [{ ...radarRun.candidates[0], input_snapshot: {
      observation_history: { membership_status: "returning", signal_status: "improved", score_comparison: {
        status: "version_changed", previous_date: "2026-07-10", previous_score: 78, score_change: null,
      } },
    } }],
  } });
  await page.goto("/daily-radar");
  await page.getByRole("button", { name: "查看細節", exact: true }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toContainText("前次策略版本不同，分數不直接比較");
  await expect(drawer).not.toContainText("觀察強度提升");
});
