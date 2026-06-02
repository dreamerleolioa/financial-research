import type {
  DailyRadarBucket,
  DailyRadarRunResponse,
  DailyRadarSymbolHistoryItem,
} from "./dailyRadarTypes";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export interface DailyRadarRunQuery {
  market?: string | null;
  bucket?: DailyRadarBucket | null;
  limit?: number | null;
}

export interface DailyRadarSymbolHistoryQuery extends DailyRadarRunQuery {
  lookback_days?: number | null;
}

type DailyRadarQuery = DailyRadarRunQuery | DailyRadarSymbolHistoryQuery;

export interface DailyRadarDisplayError {
  message: string;
  status?: number;
  detail?: unknown;
}

export class DailyRadarApiError extends Error implements DailyRadarDisplayError {
  status?: number;
  detail?: unknown;

  constructor(message: string, options: { status?: number; detail?: unknown } = {}) {
    super(message);
    this.name = "DailyRadarApiError";
    this.status = options.status;
    this.detail = options.detail;
  }
}

export function toDailyRadarDisplayError(error: unknown): DailyRadarDisplayError {
  if (error instanceof DailyRadarApiError) {
    return { message: error.message, status: error.status, detail: error.detail };
  }
  if (error instanceof Error) {
    return { message: error.message };
  }
  return { message: "Daily Radar 觀察資料讀取失敗，請稍後再試。" };
}

export async function fetchLatestDailyRadarRun(
  query: DailyRadarRunQuery = {},
): Promise<DailyRadarRunResponse> {
  return requestDailyRadar<DailyRadarRunResponse>(buildDailyRadarUrl("/daily-radar/latest", query));
}

export async function fetchDailyRadarRunByDate(
  runDate: string,
  query: DailyRadarRunQuery = {},
): Promise<DailyRadarRunResponse> {
  return requestDailyRadar<DailyRadarRunResponse>(
    buildDailyRadarUrl(`/daily-radar/${encodeURIComponent(runDate)}`, query),
  );
}

export async function fetchDailyRadarSymbolHistory(
  symbol: string,
  query: DailyRadarSymbolHistoryQuery = {},
): Promise<DailyRadarSymbolHistoryItem[]> {
  return requestDailyRadar<DailyRadarSymbolHistoryItem[]>(
    buildDailyRadarUrl(`/daily-radar/symbol/${encodeURIComponent(symbol)}`, query),
  );
}

function buildDailyRadarUrl(pathname: string, query: DailyRadarQuery): string {
  const normalizedBase = API_BASE.replace(/\/$/, "");
  const search = serializeDailyRadarQuery(query);
  return `${normalizedBase}${pathname}${search}`;
}

function serializeDailyRadarQuery(query: DailyRadarQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value == null) continue;
    params.set(key, String(value));
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

async function requestDailyRadar<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw await dailyRadarErrorFromResponse(response);
  }
  return response.json() as Promise<T>;
}

async function dailyRadarErrorFromResponse(response: Response): Promise<DailyRadarApiError> {
  const payload = await readDailyRadarErrorPayload(response);
  const detail = extractErrorDetail(payload);
  const message =
    typeof detail === "string" && detail.trim()
      ? detail
      : `Daily Radar 觀察資料讀取失敗（狀態碼 ${response.status}）。`;
  return new DailyRadarApiError(message, { status: response.status, detail });
}

async function readDailyRadarErrorPayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractErrorDetail(payload: unknown): unknown {
  if (payload && typeof payload === "object" && "detail" in payload) {
    return (payload as { detail: unknown }).detail;
  }
  return payload;
}
