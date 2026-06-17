export const API_BASE_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as string;

export const APP_BASE_URL = import.meta.env.BASE_URL;

export function appPath(pathname: string): string {
  const normalizedBase = APP_BASE_URL.endsWith("/") ? APP_BASE_URL : `${APP_BASE_URL}/`;
  return `${normalizedBase}${pathname.replace(/^\//, "")}`;
}
