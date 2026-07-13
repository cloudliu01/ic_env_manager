import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() } }));

describe('Manager control-plane audit', () => {
  beforeEach(() => {
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    window.history.replaceState({}, '', '/audit');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['fleet.v2', 'agent-registry.v2'] };
      if (path.startsWith('/api/control-plane/audit?')) return { events: [{
        id: 7, timestamp: '2026-07-13T08:00:00Z', actor_id: 'local-admin', source_addr: '127.0.0.1',
        agent_id: 'agent-a', operation: 'agents.v2.probe', target: 'agent:agent-a', result: 'success',
        dispatch_state: 'dispatched', upstream_status: 200, correlation_id: 'corr-safe', failure_category: null,
      }] };
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  afterEach(() => cleanup());

  it('renders safe control-plane events with an Agent drill-down', async () => {
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Control-plane Audit' })).toBeTruthy();
    expect(await screen.findByText('agents.v2.probe')).toBeTruthy();
    expect(screen.getByText('corr-safe')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'agent-a' }).getAttribute('href')).toBe('/audit?agent_id=agent-a');
    expect(screen.getByRole('option', { name: 'Pending' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Failure' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Denied' })).toBeTruthy();
    expect(screen.queryByText(/route is ready/i)).toBeNull();
  });

  it('keeps audit filters in the URL and sends only supported query fields', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('agents.v2.probe');

    await user.type(screen.getByLabelText('Agent filter'), 'agent-a');
    await user.type(screen.getByLabelText('Operation filter'), 'agents.v2.probe');
    await user.selectOptions(screen.getByLabelText('Result filter'), 'failed');
    await user.type(screen.getByLabelText('Correlation ID filter'), 'corr-2');

    await waitFor(() => expect(window.location.search).toContain('correlation_id=corr-2'));
    await waitFor(() => expect(apiRequest.mock.calls.some(([path]) => path === '/api/control-plane/audit?limit=100&agent_id=agent-a&operation=agents.v2.probe&result=failed&correlation_id=corr-2')).toBe(true));
  });

  it('shows a clear empty state', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['fleet.v2'] };
      if (path.startsWith('/api/control-plane/audit?')) return { events: [] };
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<App />);
    expect(await screen.findByText('No control-plane audit events match these filters.')).toBeTruthy();
  });
});
