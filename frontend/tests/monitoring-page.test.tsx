import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MetricsPage } from '../src/features/metrics/MetricsPage';
import { App } from '../src/app/App';

const getAgentMonitoringSnapshot = vi.hoisted(() => vi.fn());
const apiRequest = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/features/metrics/api', () => ({ getMonitoringSnapshot: getAgentMonitoringSnapshot }));

vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));

beforeEach(() => {
  getAgentMonitoringSnapshot.mockReset();
  getAgentMonitoringSnapshot.mockImplementation(async (agentId: string) => ({
    host_id: agentId,
    name: 'Alpha host',
    address: '127.0.0.1',
    hostname: 'alpha',
    status: 'online',
    sampled_at: '2026-06-13T00:00:00Z',
    cpu: { percent: 12.5, cores_logical: 8, cores_physical: 4, load_average: [1.1, 1.2, 1.3] },
    memory: { used_bytes: 4 * 1024 ** 3, total_bytes: 16 * 1024 ** 3, available_bytes: 12 * 1024 ** 3, percent: 25 },
    swap: { used_bytes: 0, total_bytes: 0, free_bytes: 0, percent: 0 },
    disks: [{ mount: '/', device: '/dev/disk1', fstype: 'apfs', used_bytes: 100 * 1024 ** 3, total_bytes: 500 * 1024 ** 3, free_bytes: 400 * 1024 ** 3, percent: 20 }],
    network: [{ interface: 'en0', rx_bytes: 1024, tx_bytes: 2048 }],
    uptime_seconds: 3660,
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('MetricsPage', () => {
  it('renders metric cards for its explicit manager target', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MetricsPage target={{ agentId: 'agent-b', name: 'Beta', capabilities: CAPABILITIES }} /></QueryClientProvider>);

    expect(await screen.findByText('Machine telemetry')).toBeTruthy();
    expect(await screen.findByText('12.5%')).toBeTruthy();
    expect(screen.getByText('25.0%')).toBeTruthy();
    expect(screen.getByText('Disk usage')).toBeTruthy();
    expect(getAgentMonitoringSnapshot).toHaveBeenCalledWith('agent-b', expect.anything());
  });
});

describe('Manager monitoring', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/monitoring');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: [] };
      if (path === '/api/v2/fleet/overview') {
        return { collected_at: '2026-07-12T08:02:00Z', agents: [{
          agent_id: 'agent-a', display_name: 'Alpha', enabled: true, endpoint: 'http://10.0.0.4:8765',
          connection_status: 'degraded', workload_status: 'stale', capabilities: ['summary.v2'],
          summary: { observations: { total: 4, critical: 1, warning: 0, stale: 2 }, services: { total: 3, running: 1, unhealthy: 2 } },
          last_error_code: 'agent_network_error',
        }, {
          agent_id: 'agent-b', display_name: 'Beta', enabled: true, connection_status: 'ready', workload_status: 'healthy',
          capabilities: ['summary.v2'], summary: { observations: { total: 0, critical: 0, warning: 0, stale: 0 }, services: { total: 1, running: 1, unhealthy: 0 } },
        }] };
      }
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  it('defaults to cached fleet problems, retains stale counts, and links to agent overview without fan-out queries', async () => {
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Monitoring' })).toBeTruthy();
    expect(await screen.findByText('Problems (1)')).toBeTruthy();
    expect(screen.getByText('1 critical')).toBeTruthy();
    expect(screen.getByText('2 stale')).toBeTruthy();
    expect(screen.getByText('2 unhealthy')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Alpha overview' }).getAttribute('href')).toBe('/agents/agent-a/overview');
    expect(screen.queryByText('Beta')).toBeNull();
    expect(apiRequest.mock.calls.map(([path]) => path)).toEqual(['/api/v2/runtime', '/api/v2/fleet/overview']);
  });
});
