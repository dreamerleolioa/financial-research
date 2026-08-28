import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  addPortfolioEntry,
  backfillLifecyclePlan,
  closePortfolioItem,
  createPortfolioItem,
  deletePortfolioItem,
  refreshPortfolioPrices,
  updateLifecyclePlan,
  updatePortfolioAccountSettings,
  updatePortfolioItem,
  type AddEntryRequest,
  type ClosePortfolioRequest,
  type CreatePortfolioRequest,
  type UpdatePortfolioRequest,
} from "../../lib/portfolioApi";
import type { BackfillLifecyclePlanRequest, PortfolioItem, PortfolioRiskSummary } from "../../lib/portfolioTypes";
import {
  markPortfolioMutationStarted,
  PORTFOLIO_MUTATION_SCOPE,
  readPortfolioMutationRevision,
} from "./mutationCoordinator";
import { portfolioKeys } from "./queryKeys";
import { clearPriceRefreshOverlay, storePriceRefreshOverlay } from "./priceRefreshOverlay";

const refreshRevisionBySummary = new WeakMap<PortfolioRiskSummary, string>();

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

function markPortfolioWriteStarted(queryClient: QueryClient): string {
  clearPriceRefreshOverlay(queryClient);
  return markPortfolioMutationStarted();
}

function recoverFromPortfolioWriteFailure(queryClient: QueryClient, id?: number): void {
  invalidatePortfolioReadData(queryClient);
  if (id != null) {
    void queryClient.invalidateQueries({ queryKey: portfolioKeys.history(id) });
    void queryClient.invalidateQueries({ queryKey: portfolioKeys.lifecyclePlan(id) });
  }
}

export function useCreatePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: (body: CreatePortfolioRequest) => createPortfolioItem(body),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: () => {
      invalidatePortfolioReadData(queryClient);
    },
    onError: () => recoverFromPortfolioWriteFailure(queryClient),
  });
}

export function useUpdatePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: ({ id, body }: { id: number; body: UpdatePortfolioRequest }) => updatePortfolioItem(id, body),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
    onError: (_error, variables) => recoverFromPortfolioWriteFailure(queryClient, variables.id),
  });
}

export function useBackfillLifecyclePlanMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: ({ id, body }: { id: number; body: BackfillLifecyclePlanRequest }) => backfillLifecyclePlan(id, body),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
    onError: (_error, variables) => recoverFromPortfolioWriteFailure(queryClient, variables.id),
  });
}

export function useUpdateLifecyclePlanMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: ({ id, body }: { id: number; body: BackfillLifecyclePlanRequest }) => updateLifecyclePlan(id, body),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
    onError: (_error, variables) => recoverFromPortfolioWriteFailure(queryClient, variables.id),
  });
}

export function useAddPortfolioEntryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: ({ id, body }: { id: number; body: AddEntryRequest }) => addPortfolioEntry(id, body),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
    onError: (_error, variables) => recoverFromPortfolioWriteFailure(queryClient, variables.id),
  });
}

export function useClosePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: ({ id, body }: { id: number; body: ClosePortfolioRequest }) => closePortfolioItem(id, body),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: (_data, variables) => {
      invalidatePortfolioItemData(queryClient, variables.id);
    },
    onError: (_error, variables) => recoverFromPortfolioWriteFailure(queryClient, variables.id),
  });
}

export function useDeletePortfolioItemMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: (id: number) => deletePortfolioItem(id),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: (_data, id) => {
      removePortfolioItemData(queryClient, id);
      invalidatePortfolioReadData(queryClient);
    },
    onError: (_error, id) => recoverFromPortfolioWriteFailure(queryClient, id),
  });
}

export function useRefreshPortfolioPricesMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: async (portfolioIds?: number[]) => {
      const revisionAtStart = readPortfolioMutationRevision();
      const previousSummary = queryClient.getQueryData<PortfolioRiskSummary>(portfolioKeys.riskSummary());
      const preservedSymbols = refreshedPriceSymbols(previousSummary);
      const effectivePortfolioIds = portfolioIds
        ? priceRefreshTargetsWithPreservedQuotes(
            portfolioIds,
            queryClient.getQueryData<PortfolioItem[]>(portfolioKeys.items()),
            previousSummary,
          )
        : undefined;
      const summary = await refreshPortfolioPrices(effectivePortfolioIds);
      if (readPortfolioMutationRevision() !== revisionAtStart) {
        clearPriceRefreshOverlay(queryClient);
        void queryClient.invalidateQueries({ queryKey: portfolioKeys.riskSummary() });
        throw new Error("持股資料已變更，本次價格刷新未套用，請重新更新價格");
      }

      const regressedSymbols = summary.price_refresh?.failed_symbols.filter((symbol) => preservedSymbols.has(symbol));
      if (regressedSymbols?.length) {
        throw new Error(`為保留先前更新價格，本次未套用：${regressedSymbols.join("、")} 更新失敗`);
      }
      refreshRevisionBySummary.set(summary, revisionAtStart);
      return summary;
    },
    onSuccess: (summary) => {
      const revisionAtStart = refreshRevisionBySummary.get(summary);
      if (!revisionAtStart || readPortfolioMutationRevision() !== revisionAtStart) {
        clearPriceRefreshOverlay(queryClient);
        void queryClient.invalidateQueries({ queryKey: portfolioKeys.riskSummary() });
        return;
      }
      storePriceRefreshOverlay(queryClient, summary, revisionAtStart);
      queryClient.setQueryData(portfolioKeys.riskSummary(), summary);
    },
  });
}

export function useUpdatePortfolioAccountSettingsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    scope: PORTFOLIO_MUTATION_SCOPE,
    mutationFn: (cashBalance: number) => updatePortfolioAccountSettings(cashBalance),
    onMutate: () => markPortfolioWriteStarted(queryClient),
    onSuccess: () => invalidatePortfolioReadData(queryClient),
    onError: () => recoverFromPortfolioWriteFailure(queryClient),
  });
}

function refreshedPriceSymbols(summary: PortfolioRiskSummary | undefined): Set<string> {
  return new Set(
    summary?.position_risks
      .filter((position) => position.price_context?.refresh_status === "refreshed")
      .map((position) => position.symbol) ?? [],
  );
}

function priceRefreshTargetsWithPreservedQuotes(
  requestedIds: number[],
  items: PortfolioItem[] | undefined,
  summary: PortfolioRiskSummary | undefined,
): number[] | undefined {
  const refreshedSymbols = refreshedPriceSymbols(summary);
  const preservedIds = items?.filter((item) => refreshedSymbols.has(item.symbol)).map((item) => item.id) ?? [];

  const targetIds = [...new Set([...requestedIds, ...preservedIds])].sort((left, right) => left - right);
  return targetIds.length <= 500 ? targetIds : undefined;
}
