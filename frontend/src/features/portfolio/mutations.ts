import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  addPortfolioEntry,
  backfillLifecyclePlan,
  closePortfolioItem,
  deletePortfolioItem,
  updateLifecyclePlan,
  updatePortfolioItem,
  type AddEntryRequest,
  type ClosePortfolioRequest,
  type UpdatePortfolioRequest,
} from "../../lib/portfolioApi";
import type { BackfillLifecyclePlanRequest } from "../../lib/portfolioTypes";
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
