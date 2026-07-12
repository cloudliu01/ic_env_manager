import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';
import { ApiClient, ApiClientError } from '../src/shared/api/client';

const terminalMounts = vi.hoisted(() => vi.fn());

vi.mock('../src/pages/LoginPage', () => ({
  LoginPage: ({ onAuthenticated }: { onAuthenticated: (actor: string) => void }) => (
    <button type="button" onClick={() => onAuthenticated('local-admin')}>Sign in</button>
  ),
}));

vi.mock('../src/pages/TerminalPage', () => ({
  TerminalPage: ({ visible }: { visible?: boolean }) => {
    terminalMounts();
    return <div aria-label="Terminal page">Terminal visible: {String(visible)}</div>;
  },
}));

vi.mock('../src/pages/AppRoutes', () => ({
  AppRoutes: () => <div>Legacy fleet application</div>,
}));

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockAgentFetch(capabilities = ['runtime.v2', 'terminals.v1', 'observations.v2', 'logs.v2'], unauthorizedPath?: string) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith('/api/v2/runtime')) {
      return response({ mode: 'agent', capabilities: ['runtime.v2'] });
    }
    if (path.endsWith('/api/v2/capabilities')) {
      return response({
        instance_id: 'd7d607bd-9d59-4351-8ef4-221e9d963fb7',
        name: 'build-node-01',
        api_version: '2',
        agent_version: '0.2.0',
        capabilities,
      });
    }
    if (unauthorizedPath && path.endsWith(unauthorizedPath)) {
      return response({ error: { code: 'unauthorized', message: 'expired', correlation_id: 'expired-id' } }, 401);
    }
    if (path.endsWith('/api/services')) {
      return response({ services: [] });
    }
    if (path.includes('/api/audit?')) {
      return response({ events: [] });
    }
    if (path.endsWith('/api/monitoring/local')) {
      return response({
        host_id: 'local', name: 'build-node-01', address: '127.0.0.1', status: 'online', sampled_at: '2026-07-11T10:00:00Z',
        cpu: { percent: 1, cores_logical: 4, cores_physical: 2, load_average: [] },
        memory: { used_bytes: 1, total_bytes: 2, percent: 50 }, swap: { used_bytes: 0, total_bytes: 1, percent: 0 },
        disks: [], network: [], uptime_seconds: 10,
      });
    }
    return response({ items: [], credentials: [] });
  }));
}

describe('runtime routing', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    window.sessionStorage.clear();
    terminalMounts.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('lands an authenticated standalone agent on terminal without a fleet selector', async () => {
    mockAgentFetch();
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    expect(await screen.findByText('Standalone Agent')).toBeTruthy();
    expect(screen.getByText('build-node-01')).toBeTruthy();
    expect(screen.getByText('d7d607bd-9d59-4351-8ef4-221e9d963fb7')).toBeTruthy();
    expect(await screen.findByLabelText('Terminal page')).toBeTruthy();
    expect(screen.queryByLabelText('Active agent')).toBeNull();
    expect(window.location.pathname).toBe('/terminal');
  });

  it('treats login as an explicit route and replaces it with terminal after authentication', async () => {
    window.history.replaceState({}, '', '/login');
    mockAgentFetch();
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    expect(await screen.findByLabelText('Terminal page')).toBeTruthy();
    expect(window.location.pathname).toBe('/terminal');
    expect(screen.queryByRole('heading', { name: 'Page not found' })).toBeNull();
  });

  it('redirects an authenticated visit to login back to terminal', async () => {
    mockAgentFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Sign in' }));
    await screen.findByLabelText('Terminal page');

    window.history.pushState({}, '', '/login');
    window.dispatchEvent(new PopStateEvent('popstate'));

    await waitFor(() => expect(window.location.pathname).toBe('/terminal'));
    expect(screen.queryByRole('heading', { name: 'Page not found' })).toBeNull();
  });

  it('returns a 401-expired deep link to login without rendering a not-found page', async () => {
    window.history.replaceState({}, '', '/logs');
    mockAgentFetch(undefined, '/api/v2/logs');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeTruthy();
    expect(window.location.pathname).toBe('/logs');
    expect(screen.queryByRole('heading', { name: 'Page not found' })).toBeNull();
  });

  it('keeps unavailable destinations visible, disabled, and explained', async () => {
    mockAgentFetch(['runtime.v2', 'terminals.v1']);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    const observations = await screen.findByRole('link', { name: /Observations/ });
    expect(observations.getAttribute('aria-disabled')).toBe('true');
    expect(observations.getAttribute('title')).toContain('observations.v2');
  });

  it('deep links to standalone pages and moves keyboard focus to the page heading', async () => {
    window.history.replaceState({}, '', '/logs');
    mockAgentFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    const heading = await screen.findByRole('heading', { name: 'Logs' });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it.each([
    ['/services', 'Services'],
    ['/metrics', 'Machine telemetry'],
    ['/audit', 'Audit Status'],
  ])('focuses the %s top-level h1 after route load', async (path, title) => {
    window.history.replaceState({}, '', path);
    mockAgentFetch(['runtime.v2', 'terminals.v1', 'services.v1', 'monitoring.snapshot.v1', 'audit.v1']);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Sign in' }));

    const heading = await screen.findByRole('heading', { level: 1, name: title });
    expect(heading.getAttribute('tabindex')).toBe('-1');
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it('routes manager mode lazily to the compatibility fleet entry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ mode: 'manager', capabilities: [] })));
    render(<App />);

    expect(await screen.findByText('Legacy fleet application')).toBeTruthy();
    expect(window.location.pathname).toBe('/fleet');
    expect(screen.queryByText('Standalone Agent')).toBeNull();
  });
});

describe('shared API client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('adds correlation IDs, forwards abort signals, and parses the v2 error envelope', async () => {
    const controller = new AbortController();
    let requestInit: RequestInit | undefined;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      requestInit = init;
      return response({ error: { code: 'storage_unavailable', message: 'try later', correlation_id: 'server-id' } }, 503);
    }));

    const client = new ApiClient();
    await expect(client.request('/api/v2/observations', { signal: controller.signal })).rejects.toMatchObject({
      status: 503,
      code: 'storage_unavailable',
      correlationId: 'server-id',
    });
    expect(new Headers(requestInit?.headers).get('X-Correlation-ID')).toMatch(/^[A-Za-z0-9._-]+$/);
    expect(requestInit?.signal).toBe(controller.signal);
  });

  it('parses legacy errors and expires the in-memory session on 401 without persisting credentials', async () => {
    const expired = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => response({ error: 'unauthorized', message: 'expired', correlation_id: 'legacy-id' }, 401)));
    const client = new ApiClient('', expired);
    client.setToken('do-not-store-this');

    await expect(client.request('/api/example')).rejects.toBeInstanceOf(ApiClientError);
    expect(expired).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem('ic-env-guard-token')).toBeNull();
  });
});
