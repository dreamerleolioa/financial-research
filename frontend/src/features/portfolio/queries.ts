import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import {
  fetchDecisionContextStatus,
  fetchLatestPortfolioHistory,
  fetchLifecyclePlan,
  fetchPortfolioItems,
  refreshPortfolioPrices,
} from "../../lib/portfolioApi";
import { portfolioKeys } from "./queryKeys";
import { clearPriceRefreshOverlay, readPriceRefreshOverlay } from "./priceRefreshOverlay";
import {
  PORTFOLIO_MUTATION_REVISION_KEY,
  readPortfolioMutationRevision,
} from "./mutationCoordinator";

export function usePortfolioItemsQuery() {
  return useQuery({
    queryKey: portfolioKeys.items(),
    queryFn: fetchPortfolioItems,
  });
}

export function usePortfolioRiskSummaryQuery() {
  const queryClient = useQueryClient();
  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== PORTFOLIO_MUTATION_REVISION_KEY) return;
      clearPriceRefreshOverlay(queryClient);
      void queryClient.invalidateQueries({ queryKey: portfolioKeys.riskSummary() });
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [queryClient]);

  return useQuery({
    queryKey: portfolioKeys.riskSummary(),
    queryFn: async () => {
      const revisionAtStart = readPortfolioMutationRevision();
      const refreshedSummary = await refreshPortfolioPrices();
      if (readPortfolioMutationRevision() !== revisionAtStart) {
        clearPriceRefreshOverlay(queryClient);
        throw new Error("持股資料已變更，正在重新載入最新價格");
      }
      if (refreshedSummary.price_refresh?.status === "complete") {
        clearPriceRefreshOverlay(queryClient);
        return refreshedSummary;
      }
      return readPriceRefreshOverlay(queryClient, refreshedSummary) ?? refreshedSummary;
    },
    retry: 1,
    staleTime: 0,
  });
}

export function useLatestPortfolioHistoryQuery() {
  return useQuery({
    queryKey: portfolioKeys.latestHistory(),
    queryFn: fetchLatestPortfolioHistory,
  });
}

export function useDecisionContextStatusQuery() {
  return useQuery({
    queryKey: portfolioKeys.decisionContext(),
    queryFn: fetchDecisionContextStatus,
  });
}

export function useLifecyclePlanQuery(id: number) {
  return useQuery({
    queryKey: portfolioKeys.lifecyclePlan(id),
    queryFn: () => fetchLifecyclePlan(id),
  });
}
