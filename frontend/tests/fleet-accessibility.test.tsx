import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));

const agent = {
  agent_id: 'alpha', display_name: 'Alpha', endpoint: 'https://10.0.0.4:8765',
  transport_profile_id: 'system-tls', enabled: true, connection_status: 'ready',
  workload_status: 'healthy', capabilities: ['observations.v2'],
};

const fleetAgent = {
  ...agent,
  summary: { observations: { total: 0, critical: 0 }, services: { total: 0, unhealthy: 0 } },
};

describe('Fleet accessibility workflow', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/agents/alpha/settings');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/alpha' && !init?.method) return { agent };
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  afterEach(() => cleanup());

  it('supports a keyboard Fleet result workflow with an announced result count', async () => {
    window.history.replaceState({}, '', '/fleet');
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/fleet/overview') return { collected_at: '2026-07-12T00:00:00Z', agents: [fleetAgent] };
      if (path === '/api/v2/agents/alpha') return { agent: fleetAgent };
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);

    const row = await screen.findByRole('row', { name: /Alpha/ });
    expect(screen.getByRole('status').textContent).toBe('1 Agent displayed.');
    row.focus();
    await user.keyboard('{Enter}');

    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBeTruthy();
  });

  it('restores keyboard focus to Remove from Manager when its dialog closes', async () => {
    const user = userEvent.setup();
    render(<App />);

    const trigger = await screen.findByRole('button', { name: 'Remove from Manager' });
    trigger.focus();
    await user.keyboard('{Enter}');

    const dialog = await screen.findByRole('dialog', { name: 'Remove from Manager' });
    await user.click(within(dialog).getAllByRole('button', { name: 'Close' })[1]);

    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
