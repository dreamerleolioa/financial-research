import { ApiError, requestJson } from "./apiClient";
import type {
  ClosedPortfolioItem,
  PositionGroupEventsResponse,
  PositionLifecycleReviewResponse,
  TradeReviewResponse,
} from "./portfolioTypes";

export const REFRESHABLE_TRADE_REVIEW_VERSIONS = new Set(["trade-review-v1", "trade-review-v2", "trade-review-v3"]);

export function fetchClosedPortfolioItems(): Promise<ClosedPortfolioItem[]> {
  return requestJson<ClosedPortfolioItem[]>("/portfolio/closed");
}

export async function fetchOrCreateTradeReview(portfolioId: number): Promise<TradeReviewResponse> {
  const reviewPath = `/portfolio/${portfolioId}/review`;
  try {
    const review = await requestJson<TradeReviewResponse>(reviewPath);
    if (!REFRESHABLE_TRADE_REVIEW_VERSIONS.has(review.review_version)) return review;
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
