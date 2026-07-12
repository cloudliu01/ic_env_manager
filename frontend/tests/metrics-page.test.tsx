import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MetricsPage } from '../src/pages/MetricsPage';

const getAgentMonitoringSnapshot = vi.hoisted(() => vi.fn());

vi.mock('../src/agents/StandaloneAgentContext', () => ({
  useStandaloneAgent: () => ({ agentId: 'local', name: 'Build node', capabilities: ['monitoring.snapshot.v1'] }),
  supportsCapability: (capabilities: string[], capability: string) => capabilities.includes(capability),
}));

vi.mock('../src/api/monitoring', () => ({ getAgentMonitoringSnapshot }));

describe('MetricsPage', () => {
  beforeEach(() => {
    getAgentMonitoringSnapshot.mockResolvedValue({
      host_id: 'local', name: 'Build node', address: '127.0.0.1', hostname: 'build-node', status: 'online', sampled_at: '2026-06-13T00:00:00Z',
      cpu: { percent: 12.5, cores_logical: 8, cores_physical: 4, load_average: [] },
      memory: { used_bytes: 4, total_bytes: 16, available_bytes: 12, percent: 25 }, swap: { used_bytes: 0, total_bytes: 0, free_bytes: 0, percent: 0 },
      disks: [], network: [], uptime_seconds: 60,
    });
  });

  afterEach(() => cleanup());

  it('loads telemetry for the standalone Agent identity', async () => {
    render(<MetricsPage />);

    expect(await screen.findByText('Machine telemetry')).toBeTruthy();
    expect(await screen.findByText('Build node')).toBeTruthy();
    expect(getAgentMonitoringSnapshot).toHaveBeenCalledWith('local', expect.anything());
  });
});
