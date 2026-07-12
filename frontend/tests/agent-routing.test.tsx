import { useEffect } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { listAgents } from '../src/api/agents';
import { listServices, startService, stopService } from '../src/api/services';
import {
  closeTerminal,
  createConnectToken,
  createTerminal,
  getTerminalHistory,
  listTerminals,
  resizeTerminal,
} from '../src/api/terminals';
import { AppRoutes } from '../src/pages/AppRoutes';

type ApiErrorBody = {
  error: string;
  message: string;
  correlation_id?: string;
};

const apiRequest = vi.hoisted(() => vi.fn());
const setToken = vi.hoisted(() => vi.fn());
const terminalMounts = vi.hoisted(() => vi.fn());
const MockApiClientError = vi.hoisted(() => class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiErrorBody,
  ) {
    super(body.message);
  }
});
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/auth/session', () => ({
  loadSessionToken: vi.fn(() => 'secret-token'),
  clearSessionToken: vi.fn(),
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

vi.mock('../src/pages/MetricsPage', () => ({
  MetricsPage: () => <div>Metrics page</div>,
}));

vi.mock('../src/pages/AuditStatusPage', () => ({
  AuditStatusPage: () => <div>Audit page</div>,
}));

function fleetResponse() {
  return {
    collected_at: '2026-06-26T00:00:00Z',
    hosts: [
      { id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES },
      { id: 'agent-b', name: 'Beta', status: 'ready', enabled: true, capabilities: CAPABILITIES },
    ],
  };
}

function servicesResponse(name: string) {
  return { services: [{ id: 'demo', name, status: 'configured', health_status: 'unknown', allowed_operations: ['start', 'stop'] }] };
}

describe('agent-scoped API helpers', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    apiRequest.mockReset();
  });

  it('loads agents and scopes service operations through the active agent path', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/agents') {
        return { agents: [{ id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES }] };
      }
      if (path === '/api/agents/agent-a/services') {
        return servicesResponse('Demo service');
      }
      return {};
    });

    await expect(listAgents()).resolves.toEqual([{ id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES }]);
    await expect(listServices('agent-a')).resolves.toHaveLength(1);
    await startService('agent-a', 'demo');
    await stopService('agent-a', 'demo');

    expect(apiRequest).toHaveBeenCalledWith('/api/agents');
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/services');
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/services/demo/start', { method: 'POST' });
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/services/demo/stop', { method: 'POST' });
  });

  it('scopes terminal operations through the active agent path', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/agents/agent-a/terminals') {
        return { terminals: [{ id: 'term-1' }] };
      }
      if (path === '/api/agents/agent-a/terminals/term-1') {
        return { id: 'term-1', status: 'closed' };
      }
      if (path === '/api/agents/agent-a/terminals/term-1/history?cursor=42') {
        return { terminal_id: 'term-1', output: 'history' };
      }
      if (path === '/api/agents/agent-a/terminals/term-1/connect-token') {
        return { ticket: 'ticket-1', expires_in_seconds: 60 };
      }
      return { id: 'term-1' };
    });

    await expect(listTerminals('agent-a')).resolves.toEqual([{ id: 'term-1' }]);
    await createTerminal('agent-a', 'Terminal 1');
    await closeTerminal('agent-a', 'term-1');
    await resizeTerminal('agent-a', 'term-1', 30, 100);
    await getTerminalHistory('agent-a', 'term-1', 42);
    await createConnectToken('agent-a', 'term-1');

    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/terminals');
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/terminals', {
      method: 'POST',
      body: JSON.stringify({ title: 'Terminal 1', rows: 24, cols: 80 }),
    });
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/terminals/term-1', { method: 'DELETE' });
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/terminals/term-1/resize', {
      method: 'POST',
      body: JSON.stringify({ rows: 30, cols: 100 }),
    });
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/terminals/term-1/history?cursor=42');
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/terminals/term-1/connect-token', { method: 'POST' });
  });

  it('encodes agent and resource IDs without losing Agent-scoped routing', async () => {
    apiRequest.mockResolvedValue({ services: [], terminals: [] });

    await listServices('lab/01');
    await startService('lab/01', 'svc/main');
    await listTerminals('lab/01');
    await closeTerminal('lab/01', 'term/1');

    expect(apiRequest).toHaveBeenCalledWith('/api/agents/lab%2F01/services');
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/lab%2F01/services/svc%2Fmain/start', { method: 'POST' });
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/lab%2F01/terminals');
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/lab%2F01/terminals/term%2F1', { method: 'DELETE' });
  });
});

describe('AppRoutes fleet routing', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    window.sessionStorage.clear();
    apiRequest.mockReset();
    setToken.mockClear();
    terminalMounts.mockClear();
  });

  it('shows fleet overview and routes selected host services through that agent', async () => {
    const user = userEvent.setup();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/fleet/overview') {
        return fleetResponse();
      }
      if (path === '/api/agents/agent-a/services') {
        return servicesResponse('Alpha service');
      }
      if (path === '/api/agents/agent-b/services') {
        return servicesResponse('Beta service');
      }
      return {};
    });

    render(<AppRoutes />);

    expect(await screen.findByRole('heading', { name: 'Fleet Overview' })).toBeTruthy();
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('/api/fleet/overview', expect.anything());

    await user.click(screen.getAllByRole('button', { name: 'Manage' })[1]);
    expect(await screen.findByText('Beta service')).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-b/services', expect.anything());

    await user.selectOptions(screen.getByLabelText('Active agent'), 'agent-a');
    expect(await screen.findByText('Alpha service')).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('/api/agents/agent-a/services', expect.anything());
  });

  it('keeps the terminal page mounted when switching host workspace sections', async () => {
    const user = userEvent.setup();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/fleet/overview') {
        return fleetResponse();
      }
      if (path === '/api/agents/agent-a/services') {
        return servicesResponse('Alpha service');
      }
      return {};
    });

    render(<AppRoutes />);
    await screen.findByText('Alpha');

    await user.click(screen.getAllByRole('button', { name: 'Manage' })[0]);
    await user.click(screen.getByRole('button', { name: 'Terminal' }));
    expect(screen.getByLabelText('Terminal page').textContent).toContain('true');
    expect(terminalMounts).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Services' }));
    expect(await screen.findByText('Alpha service')).toBeTruthy();
    expect(screen.getByLabelText('Terminal page').textContent).toContain('false');

    await user.click(screen.getByRole('button', { name: 'Terminal' }));
    expect(screen.getByLabelText('Terminal page').textContent).toContain('true');
    expect(terminalMounts).toHaveBeenCalledTimes(1);
  });
});
