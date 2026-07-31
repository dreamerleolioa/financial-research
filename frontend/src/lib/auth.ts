export const AUTH_TOKEN_STORAGE_KEY = "auth_token";

export function getToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}
