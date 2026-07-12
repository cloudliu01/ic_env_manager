export type LegacyApiError = {
  error: string;
  message: string;
  correlation_id?: string;
};

export type V2ApiError = {
  error: {
    code: string;
    message: string;
    correlation_id?: string;
  };
};

function isLegacyApiError(body: LegacyApiError | V2ApiError): body is LegacyApiError {
  return typeof body.error === 'string';
}

export class ApiClientError extends Error {
  readonly status: number;
  readonly body: LegacyApiError | V2ApiError;
  readonly code: string;
  readonly correlationId?: string;

  constructor(status: number, body: LegacyApiError | V2ApiError) {
    let message: string;
    let code: string;
    let correlationId: string | undefined;
    if (isLegacyApiError(body)) {
      message = body.message;
      code = body.error;
      correlationId = body.correlation_id;
    } else {
      message = body.error.message;
      code = body.error.code;
      correlationId = body.error.correlation_id;
    }
    super(message);
    this.status = status;
    this.body = body;
    this.code = code;
    this.correlationId = correlationId;
    this.name = 'ApiClientError';
  }
}

export function isApiClientError(error: unknown): error is ApiClientError {
  return error instanceof ApiClientError;
}

function correlationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isErrorBody(value: unknown): value is LegacyApiError | V2ApiError {
  if (!value || typeof value !== 'object' || !('error' in value)) {
    return false;
  }
  const error = (value as { error: unknown }).error;
  return typeof error === 'string'
    ? typeof (value as { message?: unknown }).message === 'string'
    : Boolean(error && typeof error === 'object'
      && typeof (error as { code?: unknown }).code === 'string'
      && typeof (error as { message?: unknown }).message === 'string');
}

export class ApiClient {
  private token: string | null = null;

  constructor(
    private readonly baseUrl = '',
    private onUnauthorized?: () => void,
  ) {}

  setToken(token: string | null): void {
    this.token = token;
  }

  setUnauthorizedHandler(handler?: () => void): void {
    this.onUnauthorized = handler;
  }

  webSocketProtocols(): string[] {
    if (!this.token) {
      return [];
    }
    const encoded = btoa(this.token).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return [`bearer.${encoded}`];
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set('Accept', 'application/json');
    headers.set('X-Correlation-ID', correlationId());
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    if (this.token) {
      headers.set('Authorization', `Bearer ${this.token}`);
    }

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    if (response.status === 204) {
      return undefined as T;
    }

    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) {
        this.token = null;
        this.onUnauthorized?.();
      }
      const safeBody: LegacyApiError | V2ApiError = isErrorBody(body)
        ? body
        : { error: 'request_failed', message: `Request failed with status ${response.status}` };
      throw new ApiClientError(response.status, safeBody);
    }
    return body as T;
  }
}

export const apiClient = new ApiClient();
