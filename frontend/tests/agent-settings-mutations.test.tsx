import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() } }));

const agent = { agent_id: 'alpha', display_name: 'Alpha', endpoint: 'https://10.0.0.4:8765', transport_profile_id: 'system-tls', enabled: true, connection_status: 'ready', workload_status: 'healthy', capabilities: [] };

describe('Agent Settings mutations', () => {
  beforeEach(() => {
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    window.history.replaceState({}, '', '/agents/alpha/settings');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/transport-profiles') return { profiles: [{ id: 'system-tls', type: 'verified_tls', security_label: 'Verified TLS', warning: null }] };
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

  it('restricts edits to configured transport profiles', async () => {
    render(<App />);
    const profile = await screen.findByLabelText('Transport profile');
    expect(profile.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'Verified TLS — system-tls' })).toBeTruthy();
  });

  it('keeps an agent_in_use removal dialog actionable', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Remove from Manager' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove from Manager' }));
    expect(await screen.findByText(/currently in use/)).toBeTruthy();
    expect(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' }).length).toBeGreaterThan(0);
  });

  it('starts and consumes credential rotation with entered SSH details and shows residual cleanup', async () => {
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/alpha' && !init?.method) return { agent };
      if (path === '/api/v2/agents/alpha/credential-rotation' && init?.method === 'POST' && String(init.body).includes('"action":"start"')) return { rotation: { enrollment_id: 'rotation-opaque', state: 'awaiting_cli' } };
      if (path === '/api/v2/agent-enrollments/rotation-opaque') return { enrollment_id: 'rotation-opaque', state: 'verified' };
      if (path === '/api/v2/agents/alpha/credential-rotation' && init?.method === 'POST' && String(init.body).includes('"action":"consume"')) return { rotation: { enrollment_id: 'rotation-opaque', state: 'consumed', residual_warning: 'Remove old credential manually' } };
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Rotate credential' }));
    await user.type(screen.getByLabelText('Rotation SSH user'), 'edaops');
    await user.type(screen.getByLabelText('Rotation SSH host'), '10.0.0.4');
    await user.click(screen.getByRole('button', { name: 'Start credential rotation' }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/alpha/credential-rotation', expect.objectContaining({ body: JSON.stringify({ action: 'start', ssh: { user: 'edaops', host: '10.0.0.4', port: 22 } }) })));
    await user.click(await screen.findByRole('button', { name: 'Apply rotated credential' }));
    expect(await screen.findByText('Remove old credential manually')).toBeTruthy();
  });
});
