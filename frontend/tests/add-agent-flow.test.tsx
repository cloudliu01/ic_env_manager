import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';
import type { EnrollmentJob } from '../src/features/agent-registry/enrollment-api';

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() } }));

const job = {
  enrollment_id: 'job-opaque-1', state: 'awaiting_cli', expires_at: '2026-07-12T12:10:00Z',
  last_error_code: null,
  cli: {
    argv: ['ic-env-guardctl', 'agent', 'enroll', '--manager-socket', '/run/ic-env-guard/manager.sock', '--enrollment-id', 'job-opaque-1', '--ssh', 'edaops@10.0.0.4:22'],
    display: "ic-env-guardctl agent enroll --manager-socket /run/ic-env-guard/manager.sock --enrollment-id job-opaque-1 --ssh edaops@10.0.0.4:22",
  },
  preview: { phases: {} },
};

describe('Add agent flow', () => {
  beforeEach(() => {
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    window.history.replaceState({}, '', '/agents/new');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/transport-profiles') return { profiles: [
        { id: 'system-tls', type: 'verified_tls', security_label: 'Verified TLS', warning: null },
        { id: 'lab-http', type: 'trusted_lan_http', security_label: 'Trusted-LAN HTTP', warning: 'trusted_lan_http_unencrypted' },
      ] };
      if (path === '/api/v2/agent-enrollments' && init?.method === 'POST') return job;
      if (path === '/api/v2/agent-enrollments/job-opaque-1') return job;
      if (path === '/api/v2/agent-enrollments/job-opaque-1/cancel') return { ...job, state: 'cancelled' };
      if (path === '/api/v2/agents' && init?.method === 'POST') return { agent: { agent_id: 'alpha' } };
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  afterEach(() => cleanup());

  it('starts automatic SSH enrollment without rendering a pending secret', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText('Display name'), 'Alpha');
    await user.type(await screen.findByLabelText('Agent URL'), 'https://10.0.0.4:8765');
    await user.type(screen.getByLabelText('SSH user'), 'edaops');
    await user.type(screen.getByLabelText('SSH host'), '10.0.0.4');
    await user.click(screen.getByRole('button', { name: 'Start enrollment' }));

    expect(await screen.findByText('Waiting for CLI')).toBeTruthy();
    expect(screen.getByText(/ic-env-guardctl agent enroll/)).toBeTruthy();
    expect(document.body.textContent).not.toContain('write-only-pending-token');
    expect(window.location.search).toContain('enrollment=job-opaque-1');
  });

  it('shows automatic progress without an unusable CLI instruction while SSH is running', async () => {
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agent-enrollments' && init?.method === 'POST') return { ...job, state: 'running', cli: undefined };
      if (path === '/api/v2/agent-enrollments/job-opaque-1') return { ...job, state: 'running', cli: undefined };
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText('Agent URL'), 'https://10.0.0.4:8765');
    await user.type(screen.getByLabelText('SSH user'), 'edaops');
    await user.type(screen.getByLabelText('SSH host'), '10.0.0.4');
    await user.click(screen.getByRole('button', { name: 'Start enrollment' }));

    expect(await screen.findByText('Automatic SSH enrollment')).toBeTruthy();
    expect(screen.queryByText(/ic-env-guardctl agent enroll/)).toBeNull();
  });

  it('cancels and clears a prior job when a Step 1 field changes', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByLabelText('Agent URL');
    window.history.pushState({}, '', '/agents/new?enrollment=job-opaque-1');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await screen.findByText('Waiting for CLI');

    await user.type(screen.getByLabelText('SSH user'), 'x');

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agent-enrollments/job-opaque-1/cancel', expect.objectContaining({ method: 'POST' })));
    expect(window.location.search).not.toContain('enrollment=');
  });

  it('uses configured transport profiles and shows the warning only for trusted-LAN HTTP', async () => {
    const user = userEvent.setup();
    render(<App />);
    const profile = await screen.findByLabelText('Transport profile');
    expect((profile as HTMLSelectElement).value).toBe('system-tls');
    expect(screen.queryByText(/Trusted-LAN connection is unencrypted/)).toBeNull();
    await screen.findByRole('option', { name: 'Trusted-LAN HTTP — lab-http' });
    await user.selectOptions(profile, 'lab-http');
    expect(screen.getByText(/Trusted-LAN connection is unencrypted/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /dismiss/i })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Use legacy token instead' }));
    expect((screen.getByLabelText('Legacy admin token') as HTMLInputElement).type).toBe('password');
  });

  it('rehydrates a discovery candidate from its opaque URL id after refresh', async () => {
    window.history.replaceState({}, '', '/agents/new?discoveryResult=result-opaque');
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/discovery/results/result-opaque') return { result: { result_id: 'result-opaque', candidate_url: 'http://10.0.0.4:8765', ip: '10.0.0.4', port: 8765, transport_profile_id: 'eda-http', status: 'new', enrollment_status: 'enrollment_required' } };
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<App />);

    await waitFor(() => expect((screen.getByLabelText('Agent URL') as HTMLInputElement).value).toBe('http://10.0.0.4:8765'));
    expect((screen.getByLabelText('SSH host') as HTMLInputElement).value).toBe('10.0.0.4');
    expect((screen.getByLabelText('SSH user') as HTMLInputElement).value).toBe('');
  });

  it('renders the verified identity preview and defaults an omitted display name from the Agent', async () => {
    window.history.replaceState({}, '', '/agents/new?enrollment=job-opaque-1');
    const verified = {
      ...job,
      state: 'verified',
      cli: undefined,
      preview: {
        phases: { identity: { status: 'success', code: null }, readiness: { status: 'warning', code: 'agent_readiness_unhealthy' } },
        agent: {
          agent_id: 'job-opaque-1', instance_id: '33333333-3333-4333-8333-333333333333', name: 'Build Agent 01',
          endpoint: 'https://10.0.0.4:8765', transport_profile_id: 'system-tls', transport_security: 'verified_tls',
          api_version: '2', agent_version: '0.3.0', capabilities: ['manager-enrollment.v1', 'summary.v2'],
          summary: { observations: { total: 3, critical: 1, warning: 0, stale: 0 }, services: { total: 2, running: 1, unhealthy: 1 } },
        },
      },
    };
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agent-enrollments/job-opaque-1') return verified;
      if (path === '/api/v2/agents' && init?.method === 'POST') return { agent: { agent_id: 'alpha' } };
      if (path.endsWith('/cancel')) throw new Error('verified jobs must not be cancelled');
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText('Build Agent 01')).toBeTruthy();
    expect(screen.getByText('https://10.0.0.4:8765')).toBeTruthy();
    expect(screen.getByText('Verified TLS')).toBeTruthy();
    expect(screen.getByText('0.3.0')).toBeTruthy();
    expect(screen.getByText('manager-enrollment.v1')).toBeTruthy();
    expect(screen.getByText('1 critical')).toBeTruthy();
    expect(screen.getByText('1 unhealthy')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('readiness');
    await user.click(screen.getByRole('button', { name: 'Save Agent' }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents', expect.objectContaining({ method: 'POST', body: JSON.stringify({ enrollment_id: 'job-opaque-1', display_name: 'Build Agent 01' }) })));
    expect(apiRequest).not.toHaveBeenCalledWith('/api/v2/agent-enrollments/job-opaque-1/cancel', expect.anything());
  });

  it.each(['success', 'failure'])('clears legacy token state and removes its password input after %s', async (outcome) => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/validate') {
        if (outcome === 'failure') throw new Error('validation failed');
        return { ...job, state: 'verified' };
      }
      if (path === '/api/v2/agent-enrollments/job-opaque-1') return { ...job, state: 'verified' };
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText('Display name'), 'Alpha');
    await user.type(screen.getByLabelText('Agent URL'), 'https://10.0.0.4:8765');
    await user.type(screen.getByLabelText('SSH user'), 'edaops');
    await user.type(screen.getByLabelText('SSH host'), '10.0.0.4');
    await user.click(screen.getByRole('button', { name: 'Use legacy token instead' }));
    await user.type(screen.getByLabelText('Legacy admin token'), 'legacy-secret-never-rendered');
    await user.click(screen.getByRole('button', { name: 'Validate legacy token' }));
    await waitFor(() => expect(screen.queryByLabelText('Legacy admin token')).toBeNull());
    expect(document.body.textContent).not.toContain('legacy-secret-never-rendered');
  });

  it('keeps only display name editable while a refreshed job is still resolving, then saves a verified job without cancellation', async () => {
    window.history.replaceState({}, '', '/agents/new?enrollment=job-opaque-1');
    let resolveJob: (value: EnrollmentJob) => void = () => undefined;
    const delayedJob = new Promise<EnrollmentJob>((resolve) => { resolveJob = resolve; });
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agent-enrollments/job-opaque-1') return delayedJob;
      if (path === '/api/v2/agents' && init?.method === 'POST') return { agent: { agent_id: 'alpha' } };
      if (path.endsWith('/cancel')) throw new Error('name edits must not cancel a resolving job');
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    const name = await screen.findByLabelText('Display name');
    expect((screen.getByLabelText('SSH host') as HTMLInputElement).disabled).toBe(true);
    await user.type(name, 'Alpha');
    expect(apiRequest).not.toHaveBeenCalledWith('/api/v2/agent-enrollments/job-opaque-1/cancel', expect.anything());
    resolveJob({ ...job, state: 'verified', preview: { phases: {}, agent: { name: 'Build Agent 01' } } });
    await user.click(await screen.findByRole('button', { name: 'Save Agent' }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents', expect.objectContaining({ method: 'POST', body: JSON.stringify({ enrollment_id: 'job-opaque-1', display_name: 'Alpha' }) })));
  });

  it('cancels and clears a verified job when a target field changes', async () => {
    window.history.replaceState({}, '', '/agents/new?enrollment=job-opaque-1');
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agent-enrollments/job-opaque-1') return { ...job, state: 'verified' };
      if (path.endsWith('/cancel')) return { ...job, state: 'cancelled' };
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('button', { name: 'Save Agent' });
    await user.type(screen.getByLabelText('SSH host'), 'x');
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agent-enrollments/job-opaque-1/cancel', expect.objectContaining({ method: 'POST' })));
    expect(window.location.search).not.toContain('enrollment=');
  });
});
