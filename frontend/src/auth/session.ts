const TOKEN_KEY = 'ic-env-guard-token';

export function saveSessionToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function loadSessionToken(): string | null {
  return window.sessionStorage.getItem(TOKEN_KEY);
}

export function clearSessionToken(): void {
  window.sessionStorage.removeItem(TOKEN_KEY);
}
