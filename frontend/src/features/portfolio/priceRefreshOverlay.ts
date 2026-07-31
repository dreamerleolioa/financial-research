import type { QueryClient } from "@tanstack/react-query";
import type { PortfolioRiskSummary } from "../../lib/portfolioTypes";
import { readPortfolioMutationRevision } from "./mutationCoordinator";
import { portfolioKeys } from "./queryKeys";

const PRICE_REFRESH_OVERLAY_MAX_AGE_MS = 10 * 60 * 1000;

interface PriceRefreshOverlay {
  revision: string;
  createdAt: number;
  structureFingerprint: string;
  summary: PortfolioRiskSummary;
}

const expiryTimers = new WeakMap<QueryClient, ReturnType<typeof setTimeout>>();

export function readPriceRefreshOverlay(
  queryClient: QueryClient,
  serverSummary: PortfolioRiskSummary,
): PortfolioRiskSummary | undefined {
  const overlay = queryClient.getQueryData<PriceRefreshOverlay>(portfolioKeys.priceRefreshOverlay());
  if (!overlay) return undefined;
  if (
    overlay.revision !== readPortfolioMutationRevision() ||
    Date.now() - overlay.createdAt > PRICE_REFRESH_OVERLAY_MAX_AGE_MS ||
    overlay.structureFingerprint !== portfolioStructureFingerprint(serverSummary)
  ) {
    clearPriceRefreshOverlay(queryClient);
    return undefined;
  }
  return overlay.summary;
}

export function storePriceRefreshOverlay(
  queryClient: QueryClient,
  summary: PortfolioRiskSummary,
  revision: string,
): void {
  clearPriceRefreshOverlay(queryClient);
  queryClient.setQueryData<PriceRefreshOverlay>(portfolioKeys.priceRefreshOverlay(), {
    revision,
    createdAt: Date.now(),
    structureFingerprint: portfolioStructureFingerprint(summary),
    summary,
  });
  const timer = setTimeout(() => {
    queryClient.removeQueries({ queryKey: portfolioKeys.priceRefreshOverlay(), exact: true });
    void queryClient.invalidateQueries({ queryKey: portfolioKeys.riskSummary() });
    expiryTimers.delete(queryClient);
  }, PRICE_REFRESH_OVERLAY_MAX_AGE_MS);
  expiryTimers.set(queryClient, timer);
}

export function clearPriceRefreshOverlay(queryClient: QueryClient): void {
  const timer = expiryTimers.get(queryClient);
  if (timer) clearTimeout(timer);
  expiryTimers.delete(queryClient);
  queryClient.removeQueries({ queryKey: portfolioKeys.priceRefreshOverlay(), exact: true });
}

function portfolioStructureFingerprint(summary: PortfolioRiskSummary): string {
  if (summary.portfolio_revision) return summary.portfolio_revision;
  const positions = summary.position_risks
    .map((position) => ({
      symbol: position.symbol,
      quantity: position.quantity,
      entryPrice: position.entry_price,
      defensePrice: position.defense_reference.price,
      defenseSource: position.defense_reference.source,
    }))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  return JSON.stringify(positions);
}
