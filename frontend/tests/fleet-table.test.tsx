import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Vitest runs tests in Node; the frontend compiler intentionally omits Node types.
import { readFileSync } from 'node:fs';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
const baseStyles = readFileSync('src/shared/styles/base.css', 'utf8');

vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() } }));

function setViewport(width: number) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: query.includes('max-width: 767px') ? width < 768 : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

const agents = [
  {
    agent_id: 'agent-a', display_name: 'Alpha', endpoint: 'http://10.0.0.4:8765', enabled: true,
    connection_status: 'degraded', workload_status: 'critical', transport_warning: 'trusted_lan_http_unencrypted',
    api_version: '2', agent_version: '2.4.0', observed_at: '2026-07-12T08:00:00Z', last_error_code: 'agent_network_error',
    capabilities: ['summary.v2'], summary: { observations: { total: 4, critical: 1, warning: 0, stale: 0 }, services: { total: 3, running: 1, unhealthy: 2 } },
  },
  {
    agent_id: 'agent-b', display_name: 'Beta', endpoint: 'https://10.0.0.5:8765', enabled: true,
    connection_status: 'ready', workload_status: 'healthy', api_version: '2', agent_version: '2.3.1', observed_at: '2026-07-12T08:01:00Z',
    capabilities: ['summary.v2'], summary: { observations: { total: 1, critical: 0, warning: 0, stale: 0 }, services: { total: 2, running: 2, unhealthy: 0 } },
  },
];

describe('Fleet table', () => {
  beforeEach(() => {
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    setViewport(1440);
    window.history.replaceState({}, '', '/fleet');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['fleet.v2'] };
      if (path === '/api/v2/fleet/overview') return { collected_at: '2026-07-12T08:02:00Z', agents };
      if (path === '/api/v2/agents/agent-a') return { agent: agents[0] };
      return { agents: [] };
    });
  });

  afterEach(() => cleanup());

  it('renders dense sortable fleet columns with status text, transport warning, and last-known row error', async () => {
    render(<App />);

    const table = await screen.findByRole('table', { name: 'Fleet agents' });
    expect(within(table).getByRole('columnheader', { name: /Agent/ }).getAttribute('aria-sort')).toBeNull();
    expect(within(table).getByRole('columnheader', { name: 'Health' }).getAttribute('aria-sort')).toBe('ascending');
    expect(within(table).getByRole('columnheader', { name: 'Health' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Transport' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Version' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Observations' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Services' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Last probe' })).toBeTruthy();

    const row = within(table).getByRole('row', { name: /Alpha/ });
    expect(within(row).getByText('Degraded')).toBeTruthy();
    expect(within(row).getByText('4 total · 1 critical · 0 warning · 0 stale')).toBeTruthy();
    expect(within(row).getByText('1 running / 3 total · 2 unhealthy')).toBeTruthy();
    expect(within(row).getByText((_content, element) => element?.tagName === 'TD' && element.textContent === 'Agent 2.4.0API 2')).toBeTruthy();
    expect(within(row).getByText('Unencrypted')).toBeTruthy();
    expect(within(row).getByText('Last error: agent_network_error')).toBeTruthy();
    expect(within(row).getByLabelText('Degraded status').querySelector('svg')).toBeTruthy();
  });

  it('uses a 48px dense row contract with independent 44px Open and actions hit targets', async () => {
    render(<App />);

    const table = await screen.findByRole('table', { name: 'Fleet agents' });
    const row = within(table).getByRole('row', { name: /Alpha/ });
    expect(row.classList).toContain('fleet-row');
    expect(within(row).getByRole('link', { name: 'Open Alpha' }).classList).toContain('fleet-open');
    expect(within(row).getByRole('button', { name: 'Actions for Alpha' }).classList).toContain('fleet-actions-trigger');
  });

  it('keeps Fleet header cells within the 48px compact contract around a 44px sort target', () => {
    expect(baseStyles).toContain('.fleet-table thead th');
    expect(baseStyles).toMatch(/\.fleet-table thead th \{[\s\S]*height: 48px;[\s\S]*padding-top: 0;[\s\S]*padding-bottom: 0;/);
    expect(baseStyles).toMatch(/\.fleet-table \.table-sort \{[\s\S]*min-height: 44px;[\s\S]*max-height: 44px;/);
  });

  it('defines semantic status styles for every emitted connection and workload state', () => {
    for (const status of ['ready', 'healthy', 'degraded', 'unavailable', 'disabled']) {
      expect(baseStyles).toContain(`.status-${status}`);
    }
  });

  it('keeps filters and sorting in the URL and exposes complete row operations', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole('table', { name: 'Fleet agents' });
    await user.selectOptions(screen.getByLabelText('Connection status'), 'degraded');
    expect(window.location.search).toContain('connection_status=degraded');
    expect(screen.queryByText('Beta')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Actions for Alpha' }));
    expect(screen.getByRole('menu')).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Disable Alpha' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Edit Alpha' }).getAttribute('href')).toBe('/agents/agent-a/settings');
    expect(screen.getByRole('menuitem', { name: 'Remove Alpha' })).toBeTruthy();

    await user.click(within(screen.getByRole('columnheader', { name: /Agent/ })).getByRole('button'));
    expect(window.location.search).toContain('sort=agent');
    expect(window.location.search).toContain('order=asc');
  });

  it('probes, disables, and removes a selected Agent through real APIs', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('table', { name: 'Fleet agents' });

    await user.click(screen.getByRole('button', { name: 'Probe Alpha' }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-a/probe', expect.objectContaining({ method: 'POST' })));

    await user.click(screen.getByRole('button', { name: 'Actions for Alpha' }));
    await user.click(screen.getByRole('menuitem', { name: 'Disable Alpha' }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-a', expect.objectContaining({ method: 'PUT', body: '{"enabled":false}' })));

    await user.click(screen.getByRole('button', { name: 'Actions for Alpha' }));
    await user.click(screen.getByRole('menuitem', { name: 'Remove Alpha' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove from Manager' });
    await user.click(within(dialog).getByRole('button', { name: 'Remove from Manager' }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-a', expect.objectContaining({ method: 'DELETE' })));
  });

  it('shows fleet status counts and filters by capability and problems', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('table', { name: 'Fleet agents' });
    expect(screen.getByText('Ready 1')).toBeTruthy();
    expect(screen.getByText('Degraded 1')).toBeTruthy();

    await user.selectOptions(screen.getByLabelText('Capability'), 'summary.v2');
    expect(window.location.search).toContain('capability=summary.v2');
    await user.selectOptions(screen.getByLabelText('Problems'), 'has-problems');
    expect(window.location.search).toContain('problem=has-problems');
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.queryByText('Beta')).toBeNull();
  });

  it('does not present a missing summary as zero workload data', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['fleet.v2'] };
      if (path === '/api/v2/fleet/overview') return { collected_at: '2026-07-12T08:02:00Z', agents: [{ ...agents[0], summary: undefined }] };
      return { agents: [] };
    });
    render(<App />);
    const row = within(await screen.findByRole('table', { name: 'Fleet agents' })).getByRole('row', { name: /Alpha/ });
    expect(within(row).getAllByText('No summary')).toHaveLength(2);
    expect(within(row).queryByText(/0 total/)).toBeNull();
  });

  it('refreshes every enabled Agent without one failure blocking the rest', async () => {
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['fleet.v2'] };
      if (path === '/api/v2/fleet/overview') return { collected_at: '2026-07-12T08:02:00Z', agents };
      if (path === '/api/v2/agents/agent-a/probe' && init?.method === 'POST') throw new Error('offline');
      if (path === '/api/v2/agents/agent-b/probe' && init?.method === 'POST') return { agent: agents[1] };
      return { agents: [] };
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('table', { name: 'Fleet agents' });
    await user.click(screen.getByRole('button', { name: 'Refresh all' }));
    expect((await screen.findByRole('status', { name: 'Fleet refresh result' })).textContent).toContain('1 refreshed; 1 failed');
    expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-b/probe', expect.objectContaining({ method: 'POST' }));
  });

  it('falls back to the table when matchMedia is unavailable', async () => {
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined });
    render(<App />);

    expect(await screen.findByRole('table', { name: 'Fleet agents' })).toBeTruthy();
  });

  it.each([375, 768, 1024, 1440])('uses the card fallback only below 768px (%ipx)', async (width) => {
    setViewport(width);
    render(<App />);

    if (width < 768) {
      expect(await screen.findByRole('list', { name: 'Fleet agents' })).toBeTruthy();
      expect(screen.queryByRole('table')).toBeNull();
      expect(screen.getByRole('link', { name: 'Open Alpha' })).toBeTruthy();
      expect(screen.queryByRole('button', { name: /Probe|Disable|Remove/ })).toBeNull();
      expect(screen.queryByRole('link', { name: 'Edit' })).toBeNull();
    } else {
      expect(await screen.findByRole('table', { name: 'Fleet agents' })).toBeTruthy();
      expect(screen.queryByRole('list', { name: 'Fleet agents' })).toBeNull();
    }
  });
});
