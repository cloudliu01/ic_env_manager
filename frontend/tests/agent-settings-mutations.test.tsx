import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));

const agent = { agent_id: 'alpha', display_name: 'Alpha', endpoint: 'https://10.0.0.4:8765', transport_profile_id: 'system-tls', enabled: true, connection_status: 'ready', workload_status: 'healthy', capabilities: [] };

describe('Agent Settings mutations', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/agents/alpha/settings');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/alpha' && !init?.method) return { agent };
      if (path === '/api/v2/agents/alpha' && init?.method === 'DELETE') throw Object.assign(new Error('Agent is in use'), { code: 'agent_in_use' });
      if (path === '/api/v2/agents/alpha' && init?.method === 'PUT') return { agent };
      throw new Error(`Unexpected request: ${path}`);
    });
  });
  afterEach(() => cleanup());

  it('requires same-identity verification for an endpoint change', async () => {
    const user = userEvent.setup();
    render(<App />);
    const url = await screen.findByLabelText('Agent URL');
    await user.clear(url);
    await user.type(url, 'https://10.0.0.5:8765');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(screen.getByRole('alert').textContent).toContain('same Agent identity');
  });

  it('keeps an agent_in_use removal dialog actionable', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Remove from Manager' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove from Manager' }));
    expect(await screen.findByText(/currently in use/)).toBeTruthy();
    expect(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' }).length).toBeGreaterThan(0);
  });
});
