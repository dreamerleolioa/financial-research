import { ApiError, requestJson } from "./apiClient";
import type {
  ClosedPortfolioItem,
  PositionGroupEventsResponse,
  PositionLifecycleReviewResponse,
  TradeReviewResponse,
} from "./portfolioTypes";

export function fetchClosedPortfolioItems(): Promise<ClosedPortfolioItem[]> {
  return requestJson<ClosedPortfolioItem[]>("/portfolio/closed");
}

export async function fetchOrCreateTradeReview(portfolioId: number): Promise<TradeReviewResponse> {
  const reviewPath = `/portfolio/${portfolioId}/review`;
  try {
    return await requestJson<TradeReviewResponse>(reviewPath);
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 404) throw err;
  }
  return requestJson<TradeReviewResponse>(reviewPath, { method: "POST" });
}

export function fetchPositionGroupEvents(positionGroupId: string): Promise<PositionGroupEventsResponse> {
  return requestJson<PositionGroupEventsResponse>(`/portfolio/groups/${positionGroupId}/events`);
}

export function createPositionLifecycleReview(positionGroupId: string): Promise<PositionLifecycleReviewResponse> {
  return requestJson<PositionLifecycleReviewResponse>(`/portfolio/groups/${positionGroupId}/lifecycle-review`, {
    method: "POST",
  });
}

