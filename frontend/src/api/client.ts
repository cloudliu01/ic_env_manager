export type ApiError = {
  error: string;
  message: string;
  correlation_id?: string;
};

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiError,
  ) {
    super(body.message);
  }
}

export class ApiClient {
  constructor(
    private readonly baseUrl = '',
    private token: string | null = null,
  ) {}

  setToken(token: string | null): void {
    this.token = token;
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
    const body = await response.json();
    if (!response.ok) {
      throw new ApiClientError(response.status, body as ApiError);
    }
    return body as T;
  }
}

export const apiClient = new ApiClient();
