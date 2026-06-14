import { useEffect } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '../src/api/client';
import { AppRoutes } from '../src/pages/AppRoutes';

type ApiErrorBody = {
  error: string;
  message: string;
  correlation_id?: string;
};

const loadSessionToken = vi.hoisted(() => vi.fn(() => 'secret-token'));
const clearSessionToken = vi.hoisted(() => vi.fn());
const apiRequest = vi.hoisted(() => vi.fn());
const setToken = vi.hoisted(() => vi.fn());
const MockApiClientError = vi.hoisted(() => class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiErrorBody,
  ) {
    super(body.message);
  }
});
const terminalMounts = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/auth/session', () => ({
  clearSessionToken,
  loadSessionToken,
}));

vi.mock('../src/api/client', () => ({
  ApiClientError: MockApiClientError,
  apiClient: {
    setToken,
    request: apiRequest,
  },
}));

vi.mock('../src/pages/TerminalPage', () => ({
  TerminalPage: ({ visible = true }: { visible?: boolean }) => {
    useEffect(() => {
      terminalMounts();
    }, []);
    return <div aria-label="Terminal page">Terminal visible: {String(visible)}</div>;
  },
}));

vi.mock('../src/pages/ServiceListPage', () => ({
  ServiceListPage: () => <div>Services page</div>,
}));

vi.mock('../src/pages/MetricsPage', () => ({
  MetricsPage: () => <div>Metrics page</div>,
}));

vi.mock('../src/pages/AuditStatusPage', () => ({
  AuditStatusPage: () => <div>Audit page</div>,
}));

describe('AppRoutes terminal navigation', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    window.sessionStorage.clear();
    loadSessionToken.mockReturnValue('secret-token');
    clearSessionToken.mockClear();
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/agents') {
        return { agents: [{ id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES }] };
      }
      return { status: 'ready' };
    });
    setToken.mockClear();
    terminalMounts.mockClear();
  });

  it('keeps the terminal page mounted when switching to another section and back', async () => {
    const user = userEvent.setup();
    render(<AppRoutes />);
    await screen.findByLabelText('Active agent');

    await user.click(screen.getByRole('button', { name: 'Terminal' }));
    expect(screen.getByLabelText('Terminal page').textContent).toContain('true');
    expect(terminalMounts).toHaveBeenCalledTimes(1);
    expect(setToken).toHaveBeenCalledWith('secret-token');

    await user.click(screen.getByRole('button', { name: 'Services' }));
    expect(screen.getByText('Services page')).toBeTruthy();
    expect(screen.getByLabelText('Terminal page').textContent).toContain('false');

    await user.click(screen.getByRole('button', { name: 'Terminal' }));
    expect(screen.getByLabelText('Terminal page').textContent).toContain('true');
    expect(terminalMounts).toHaveBeenCalledTimes(1);
  });

  it('clears a stale stored token and returns to login when agents are unauthorized', async () => {
    let rejectAgents = (_reason: Error): void => {
      throw new Error('Expected AppRoutes to request the agent list');
    };
    window.sessionStorage.setItem('activeAgentId', 'local-agent');
    loadSessionToken.mockReturnValue('definitely-wrong-token');
    apiRequest.mockImplementation((path: string) => {
      if (path === '/api/agents') {
        return new Promise((_resolve, reject) => {
          rejectAgents = reject;
        });
      }
      return Promise.resolve({ status: 'ready' });
    });

    render(<AppRoutes />);

    expect(await screen.findByText('Signed in as local-admin')).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('/api/agents');
    const rejectAgentList = rejectAgents;

    await act(async () => {
      rejectAgentList(new ApiClientError(401, { error: 'unauthorized', message: 'Invalid bearer token' }));
    });

    expect(await screen.findByLabelText('Generated local bearer token')).toBeTruthy();
    expect(screen.queryByText('Signed in as local-admin')).toBeNull();
    expect(clearSessionToken).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem('activeAgentId')).toBeNull();
    expect(setToken).toHaveBeenCalledWith('definitely-wrong-token');
    expect(setToken).toHaveBeenCalledWith(null);
  });
});
