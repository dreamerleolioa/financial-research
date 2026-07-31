import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  addPortfolioEntry,
  backfillLifecyclePlan,
  closePortfolioItem,
  deletePortfolioItem,
  refreshPortfolioPrices,
  updateLifecyclePlan,
  updatePortfolioItem,
  type AddEntryRequest,
  type ClosePortfolioRequest,
  type UpdatePortfolioRequest,
} from "../../lib/portfolioApi";
import type { BackfillLifecyclePlanRequest, PortfolioItem, PortfolioRiskSummary } from "../../lib/portfolioTypes";
import { portfolioKeys } from "./queryKeys";

function invalidatePortfolioReadData(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: portfolioKeys.items() });
  void queryClient.invalidateQueries({ queryKey: portfolioKeys.riskSummary() });
  void queryClient.invalidateQueries({ queryKey: portfolioKeys.latestHistory() });
  void queryClient.invalidateQueries({ queryKey: portfolioKeys.decisionContext() });
}

function invalidatePortfolioItemData(queryClient: QueryClient, id: number): void {
  invalidatePortfolioReadData(queryClient);
  void queryClient.invalidateQueries({ queryKey: portfolioKeys.history(id) });
  void queryClient.invalidateQueries({ queryKey: portfolioKeys.lifecyclePlan(id) });
}

function removePortfolioItemData(queryClient: QueryClient, id: number): void {
  queryClient.removeQueries({ queryKey: portfolioKeys.history(id) });
  queryClient.removeQueries({ queryKey: portfolioKeys.lifecyclePlan(id) });
}

export function useUpdatePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: UpdatePortfolioRequest }) => updatePortfolioItem(id, body),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
  });
}

export function useBackfillLifecyclePlanMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: BackfillLifecyclePlanRequest }) => backfillLifecyclePlan(id, body),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
  });
}

export function useUpdateLifecyclePlanMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: BackfillLifecyclePlanRequest }) => updateLifecyclePlan(id, body),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
  });
}

export function useAddPortfolioEntryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: AddEntryRequest }) => addPortfolioEntry(id, body),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
  });
}

export function useClosePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ClosePortfolioRequest }) => closePortfolioItem(id, body),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
  });
}

export function useDeletePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deletePortfolioItem(id),
    onSuccess: (_data, id) => {
      removePortfolioItemData(queryClient, id);
      invalidatePortfolioReadData(queryClient);
    },
  });
}

export function useRefreshPortfolioPricesMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (portfolioIds?: number[]) => {
      const effectivePortfolioIds = portfolioIds
        ? priceRefreshTargetsWithPreservedQuotes(
            portfolioIds,
            queryClient.getQueryData<PortfolioItem[]>(portfolioKeys.items()),
            queryClient.getQueryData<PortfolioRiskSummary>(portfolioKeys.riskSummary()),
          )
        : undefined;
      return refreshPortfolioPrices(effectivePortfolioIds);
    },
    onSuccess: (summary) => {
      queryClient.setQueryData(portfolioKeys.riskSummary(), summary);
    },
  });
}

function priceRefreshTargetsWithPreservedQuotes(
  requestedIds: number[],
  items: PortfolioItem[] | undefined,
  summary: PortfolioRiskSummary | undefined,
): number[] {
  const refreshedSymbols = new Set(
    summary?.position_risks
      .filter((position) => position.price_context?.refresh_status === "refreshed")
      .map((position) => position.symbol) ?? [],
  );
  const preservedIds = items?.filter((item) => refreshedSymbols.has(item.symbol)).map((item) => item.id) ?? [];

  return [...new Set([...requestedIds, ...preservedIds])].sort((left, right) => left - right);
}
