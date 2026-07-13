import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() } }));

describe('Manager application routes', () => {
  afterEach(() => cleanup());

  it('uses the Router fleet entry without an active-Agent session selection', async () => {
    window.sessionStorage.clear();
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    window.history.replaceState({}, '', '/');
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['fleet.v2'] };
      return { agents: [], collected_at: '2026-07-12T00:00:00Z' };
    });

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Fleet' })).toBeTruthy();
    expect(window.location.pathname).toBe('/fleet');
    expect(window.sessionStorage.getItem('activeAgentId')).toBeNull();
  });
});
