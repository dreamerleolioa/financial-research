import { requestJson } from "./apiClient";

export interface WatchlistItem {
  id: number;
  symbol: string;
  name: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateWatchlistRequest {
  symbol: string;
  notes?: string | null;
}

export interface UpdateWatchlistRequest {
  notes?: string | null;
}

export function fetchWatchlistItems(): Promise<WatchlistItem[]> {
  return requestJson<WatchlistItem[]>("/watchlist");
}

export function createWatchlistItem(body: CreateWatchlistRequest): Promise<WatchlistItem> {
  return requestJson<WatchlistItem>("/watchlist", {
    method: "POST",
    body,
  });
}

export function updateWatchlistItem(id: number, body: UpdateWatchlistRequest): Promise<WatchlistItem> {
  return requestJson<WatchlistItem>(`/watchlist/${id}`, {
    method: "PUT",
    body,
  });
}

export function deleteWatchlistItem(id: number): Promise<void> {
  return requestJson<void>(`/watchlist/${id}`, {
    method: "DELETE",
  });
}
