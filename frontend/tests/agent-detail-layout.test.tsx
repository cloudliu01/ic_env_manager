import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));

const detail = {
  agent_id: 'agent-a', display_name: 'Alpha', endpoint: 'http://10.0.0.4:8765', enabled: true,
  transport_profile_id: 'trusted-lan', transport_warning: 'trusted_lan_http_unencrypted',
  connection_status: 'degraded', workload_status: 'critical', observed_at: '2026-07-12T08:00:00Z',
  agent_version: '2.4.0', capabilities: ['observations.v2'], last_error_code: 'agent_network_error',
  summary: { observations: { total: 4, critical: 1, warning: 0, stale: 0 }, services: { total: 3, running: 1, unhealthy: 2 } },
};

function renderManagerAt(path: string) {
  window.history.replaceState({}, '', path);
  return render(<App />);
}

describe('Agent detail shell', () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: [] };
      if (path === '/api/v2/agents/agent-a') return { agent: detail };
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  afterEach(() => cleanup());

  it('uses the route agent detail for separate connection and workload status while keeping the trusted-LAN warning visible', async () => {
    renderManagerAt('/agents/agent-a/overview');

    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Connection' })).toBeTruthy();
    expect(screen.getAllByText('Degraded')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Workload' })).toBeTruthy();
    expect(screen.getAllByText('Critical')).toHaveLength(2);
    expect(screen.getAllByRole('alert').some((alert) => alert.textContent?.includes('Trusted-LAN connection is unencrypted'))).toBe(true);
    expect(screen.getByText('1 critical')).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-a', expect.anything());
  });

  it('keeps unavailable capability tabs visible with a reason and renders route-specific settings', async () => {
    renderManagerAt('/agents/agent-a/settings');

    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeTruthy();
    expect(screen.getByText('http://10.0.0.4:8765')).toBeTruthy();
    const logs = screen.getByRole('link', { name: /Logs/ });
    expect(logs.getAttribute('aria-disabled')).toBe('true');
    expect(logs.getAttribute('title')).toContain('logs.v2');
  });
});
