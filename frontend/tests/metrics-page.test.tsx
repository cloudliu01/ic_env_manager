import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MetricsPage } from '../src/pages/MetricsPage';

const activeAgent = vi.hoisted(() => ({ id: 'agent-a' as string | null }));
const getAgentMonitoringSnapshot = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/agents/AgentStateContext', () => ({
  useActiveAgent: () => ({
    activeAgentId: activeAgent.id,
    activeAgent: activeAgent.id ? { id: activeAgent.id, name: activeAgent.id === 'agent-b' ? 'Beta agent' : 'Alpha agent', enabled: true, status: 'ready', capabilities: CAPABILITIES } : null,
  }),
  agentSupports: (agent: { capabilities?: string[] } | null, capability: string) => Boolean(agent?.capabilities?.includes(capability)),
}));

vi.mock('../src/api/monitoring', () => ({
  getAgentMonitoringSnapshot,
}));

function snapshot(agentId: string) {
  return {
    host_id: agentId,
    name: agentId === 'agent-b' ? 'Beta host' : 'Alpha host',
    address: '127.0.0.1',
    hostname: agentId === 'agent-b' ? 'beta' : 'alpha',
    status: 'online',
    sampled_at: '2026-06-13T00:00:00Z',
    cpu: { percent: agentId === 'agent-b' ? 55.5 : 12.5, cores_logical: 8, cores_physical: 4, load_average: [1.1, 1.2, 1.3] },
    memory: { used_bytes: 4 * 1024 ** 3, total_bytes: 16 * 1024 ** 3, available_bytes: 12 * 1024 ** 3, percent: 25 },
    swap: { used_bytes: 0, total_bytes: 0, free_bytes: 0, percent: 0 },
    disks: [{ mount: '/', device: '/dev/disk1', fstype: 'apfs', used_bytes: 100 * 1024 ** 3, total_bytes: 500 * 1024 ** 3, free_bytes: 400 * 1024 ** 3, percent: 20 }],
    network: [{ interface: 'en0', rx_bytes: 1024, tx_bytes: 2048 }],
    uptime_seconds: 3660,
  };
}

describe('MetricsPage', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    activeAgent.id = 'agent-a';
    getAgentMonitoringSnapshot.mockReset();
    getAgentMonitoringSnapshot.mockImplementation(async (agentId: string) => snapshot(agentId));
  });

  it('shows active-agent monitoring dashboard data without machine credentials', async () => {
    render(<MetricsPage />);

    expect(await screen.findByText('Machine telemetry')).toBeTruthy();
    expect(await screen.findByText('12.5%')).toBeTruthy();
    expect(screen.getByText('Alpha host')).toBeTruthy();
    expect(screen.getByText('Disk usage')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Bearer token')).toBeNull();
    expect(screen.queryByRole('button', { name: /add machine/i })).toBeNull();
    expect(getAgentMonitoringSnapshot).toHaveBeenCalledWith('agent-a', expect.anything());
  });

  it('reloads snapshots when the active agent changes', async () => {
    const { rerender } = render(<MetricsPage />);

    expect(await screen.findByText('Alpha host')).toBeTruthy();

    activeAgent.id = 'agent-b';
    rerender(<MetricsPage />);

    expect(await screen.findByText('Beta host')).toBeTruthy();
    expect(screen.queryByText('Alpha host')).toBeNull();
    await waitFor(() => expect(getAgentMonitoringSnapshot).toHaveBeenCalledWith('agent-b', expect.anything()));
  });

  it('reports when no active agent is selected', async () => {
    activeAgent.id = null;

    render(<MetricsPage />);

    expect(await screen.findByText('No active agent selected.')).toBeTruthy();
    expect(getAgentMonitoringSnapshot).not.toHaveBeenCalled();
  });
});
