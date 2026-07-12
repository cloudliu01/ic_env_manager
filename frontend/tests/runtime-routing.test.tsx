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

function mockAgentFetch(capabilities = ['runtime.v2', 'terminals.v1', 'observations.v2', 'logs.v2']) {
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
    expect(screen.getByLabelText('Terminal page')).toBeTruthy();
    expect(screen.queryByLabelText('Active agent')).toBeNull();
    expect(window.location.pathname).toBe('/terminal');
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
