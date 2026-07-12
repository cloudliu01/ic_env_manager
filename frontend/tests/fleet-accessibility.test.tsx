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
