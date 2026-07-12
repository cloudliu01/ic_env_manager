import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));

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
    agent_version: '2.4.0', observed_at: '2026-07-12T08:00:00Z', last_error_code: 'agent_network_error',
    capabilities: ['summary.v2'], summary: { observations: { total: 4, critical: 1, warning: 0, stale: 0 }, services: { total: 3, running: 1, unhealthy: 2 } },
  },
  {
    agent_id: 'agent-b', display_name: 'Beta', endpoint: 'https://10.0.0.5:8765', enabled: true,
    connection_status: 'ready', workload_status: 'healthy', agent_version: '2.3.1', observed_at: '2026-07-12T08:01:00Z',
    capabilities: ['summary.v2'], summary: { observations: { total: 1, critical: 0, warning: 0, stale: 0 }, services: { total: 2, running: 2, unhealthy: 0 } },
  },
];

describe('Fleet table', () => {
  beforeEach(() => {
    setViewport(1440);
    window.history.replaceState({}, '', '/fleet');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: [] };
      if (path === '/api/v2/fleet/overview') return { collected_at: '2026-07-12T08:02:00Z', agents };
      if (path === '/api/v2/agents/agent-a') return { agent: agents[0] };
      return { agents: [] };
    });
  });

  afterEach(() => cleanup());

  it('renders dense sortable fleet columns with status text, transport warning, and last-known row error', async () => {
    render(<App />);

    const table = await screen.findByRole('table', { name: 'Fleet agents' });
    expect(within(table).getByRole('columnheader', { name: /Agent/ }).getAttribute('aria-sort')).toBe('ascending');
    expect(within(table).getByRole('columnheader', { name: 'Health' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Transport' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Version' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Observations' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Services' })).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Last probe' })).toBeTruthy();

    const row = within(table).getByRole('row', { name: /Alpha/ });
    expect(within(row).getByText('Degraded')).toBeTruthy();
    expect(within(row).getByText('1 critical')).toBeTruthy();
    expect(within(row).getByText('Unencrypted')).toBeTruthy();
    expect(within(row).getByText('Last error: agent_network_error')).toBeTruthy();
    expect(within(row).getByLabelText('Degraded status').querySelector('svg')).toBeTruthy();
  });

  it('keeps filters in the URL and exposes only non-mutating row entry points', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole('table', { name: 'Fleet agents' });
    await user.selectOptions(screen.getByLabelText('Connection status'), 'degraded');
    expect(window.location.search).toContain('connection_status=degraded');
    expect(screen.queryByText('Beta')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Actions for Alpha' }));
    expect(screen.getByRole('menu')).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Probe Alpha' })).toBeTruthy();
    await user.click(screen.getByRole('link', { name: 'Open Alpha' }));
    expect(window.location.pathname).toBe('/agents/agent-a/overview');
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
    } else {
      expect(await screen.findByRole('table', { name: 'Fleet agents' })).toBeTruthy();
      expect(screen.queryByRole('list', { name: 'Fleet agents' })).toBeNull();
    }
  });
});
