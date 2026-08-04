import { getToken } from "./auth";
import { API_BASE_URL } from "./config";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  retryAfterSeconds: number | null;

  constructor(message: string, status: number, detail: unknown = null, retryAfterSeconds: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  auth?: boolean;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
}

export function apiUrl(pathname: string, query?: ApiRequestOptions["query"]): string {
  const normalizedPath = pathname.startsWith("/") ? pathname : `/${pathname}`;
  const url = new URL(`${API_BASE_URL}${normalizedPath}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value != null) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function requestJson<T>(pathname: string, options: ApiRequestOptions = {}): Promise<T> {
  const { auth = true, body, headers, query, ...init } = options;
  const response = await fetch(apiUrl(pathname, query), {
    ...init,
    headers: buildHeaders(headers, auth, body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function buildHeaders(headers: HeadersInit | undefined, includeAuth: boolean, hasJsonBody: boolean): HeadersInit {
  const builtHeaders = new Headers(headers);

  if (hasJsonBody && !builtHeaders.has("Content-Type")) {
    builtHeaders.set("Content-Type", "application/json");
  }

  if (includeAuth && !builtHeaders.has("Authorization")) {
    const token = getToken();
    if (token) builtHeaders.set("Authorization", `Bearer ${token}`);
  }

  return builtHeaders;
}

async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  const detail = await readErrorPayload(response);
  const message = extractErrorMessage(detail) ?? `HTTP ${response.status}`;
  return new ApiError(message, response.status, detail, parseRetryAfterSeconds(response.headers.get("Retry-After")));
}

function parseRetryAfterSeconds(value: string | null): number | null {
  if (value == null || value.trim() === "") return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds;
  const retryAt = Date.parse(value);
  if (!Number.isFinite(retryAt)) return null;
  return Math.max(0, (retryAt - Date.now()) / 1000);
}

async function readErrorPayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractErrorMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    const nestedDetail = (detail as { detail: unknown }).detail;
    if (typeof nestedDetail === "string" && nestedDetail.trim()) return nestedDetail;
  }
  return null;
}
