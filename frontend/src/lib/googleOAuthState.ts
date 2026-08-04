const GOOGLE_OAUTH_STATE_STORAGE_KEY = "google_oauth_state";

export function createGoogleOAuthState(): string {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function storeGoogleOAuthState(state: string): void {
  window.sessionStorage.setItem(GOOGLE_OAUTH_STATE_STORAGE_KEY, state);
}

export function consumeGoogleOAuthState(returnedState: string | null): boolean {
  const expectedState = window.sessionStorage.getItem(GOOGLE_OAUTH_STATE_STORAGE_KEY);
  window.sessionStorage.removeItem(GOOGLE_OAUTH_STATE_STORAGE_KEY);
  return expectedState !== null && returnedState !== null && returnedState === expectedState;
}
