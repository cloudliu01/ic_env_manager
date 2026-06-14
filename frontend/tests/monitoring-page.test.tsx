import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MetricsPage } from '../src/pages/MetricsPage';

const getAgentMonitoringSnapshot = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/agents/AgentContext', () => ({
  useActiveAgent: () => ({
    activeAgentId: 'agent-a',
    activeAgent: { id: 'agent-a', name: 'Alpha agent', enabled: true, status: 'ready', capabilities: CAPABILITIES },
  }),
  agentSupports: (agent: { capabilities?: string[] } | null, capability: string) => Boolean(agent?.capabilities?.includes(capability)),
}));

vi.mock('../src/api/monitoring', () => ({
  getAgentMonitoringSnapshot,
}));

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
  it('renders local monitoring metric cards', async () => {
    render(<MetricsPage />);

    expect(await screen.findByText('Machine telemetry')).toBeTruthy();
    expect(await screen.findByText('12.5%')).toBeTruthy();
    expect(screen.getByText('25.0%')).toBeTruthy();
    expect(screen.getByText('Disk usage')).toBeTruthy();
  });
});
