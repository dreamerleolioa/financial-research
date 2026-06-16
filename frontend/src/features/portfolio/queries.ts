import { useQuery } from "@tanstack/react-query";
import {
  fetchDecisionContextStatus,
  fetchLatestPortfolioHistory,
  fetchLifecyclePlan,
  fetchPortfolioItems,
  fetchPortfolioRiskSummary,
} from "../../lib/portfolioApi";
import { portfolioKeys } from "./queryKeys";

export function usePortfolioItemsQuery() {
  return useQuery({
    queryKey: portfolioKeys.items(),
    queryFn: fetchPortfolioItems,
  });
}

export function usePortfolioRiskSummaryQuery() {
  return useQuery({
    queryKey: portfolioKeys.riskSummary(),
    queryFn: fetchPortfolioRiskSummary,
    retry: 1,
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
