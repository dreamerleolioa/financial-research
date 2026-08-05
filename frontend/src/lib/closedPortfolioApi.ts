import { ApiError, requestJson } from "./apiClient";
import type {
  ClosedPortfolioItem,
  PositionGroupEventsResponse,
  PositionLifecycleReviewResponse,
  TradeReviewResponse,
} from "./portfolioTypes";

export const REFRESHABLE_TRADE_REVIEW_VERSIONS = new Set(["trade-review-v1", "trade-review-v2", "trade-review-v3"]);
const TRADE_REVIEW_CONFLICT_MAX_ATTEMPTS = 12;
const TRADE_REVIEW_DEFAULT_RETRY_SECONDS = 1;
const TRADE_REVIEW_MAX_RETRY_SECONDS = 5;

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
  return createTradeReviewWithConflictRetry(reviewPath);
}

async function createTradeReviewWithConflictRetry(reviewPath: string): Promise<TradeReviewResponse> {
  for (let attempt = 1; attempt <= TRADE_REVIEW_CONFLICT_MAX_ATTEMPTS; attempt += 1) {
    try {
      return await requestJson<TradeReviewResponse>(reviewPath, { method: "POST" });
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 409 || attempt === TRADE_REVIEW_CONFLICT_MAX_ATTEMPTS) {
        throw err;
      }
      const retrySeconds = Math.min(
        TRADE_REVIEW_MAX_RETRY_SECONDS,
        Math.max(0, err.retryAfterSeconds ?? TRADE_REVIEW_DEFAULT_RETRY_SECONDS),
      );
      await new Promise((resolve) => setTimeout(resolve, retrySeconds * 1000));
    }
  }
  throw new Error("Unreachable trade review retry state");
}

export function fetchPositionGroupEvents(positionGroupId: string): Promise<PositionGroupEventsResponse> {
  return requestJson<PositionGroupEventsResponse>(`/portfolio/groups/${positionGroupId}/events`);
}

export function createPositionLifecycleReview(positionGroupId: string): Promise<PositionLifecycleReviewResponse> {
  return requestJson<PositionLifecycleReviewResponse>(`/portfolio/groups/${positionGroupId}/lifecycle-review`, {
    method: "POST",
  });
}
