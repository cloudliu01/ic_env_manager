import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MetricsPage } from '../src/pages/MetricsPage';

vi.mock('../src/api/monitoring', () => ({
  listMachines: vi.fn(async () => [
    { id: 'local', name: 'Local host', address: '127.0.0.1', port: null, endpoint: 'local', is_local: true, created_at: null, updated_at: null },
  ]),
  getMachineSnapshot: vi.fn(async () => ({
    host_id: 'local',
    name: 'Local host',
    address: '127.0.0.1',
    hostname: 'local',
    status: 'online',
    sampled_at: '2026-06-13T00:00:00Z',
    cpu: { percent: 12.5, cores_logical: 8, cores_physical: 4, load_average: [1.1, 1.2, 1.3] },
    memory: { used_bytes: 4 * 1024 ** 3, total_bytes: 16 * 1024 ** 3, available_bytes: 12 * 1024 ** 3, percent: 25 },
    swap: { used_bytes: 0, total_bytes: 0, free_bytes: 0, percent: 0 },
    disks: [{ mount: '/', device: '/dev/disk1', fstype: 'apfs', used_bytes: 100 * 1024 ** 3, total_bytes: 500 * 1024 ** 3, free_bytes: 400 * 1024 ** 3, percent: 20 }],
    network: [{ interface: 'en0', rx_bytes: 1024, tx_bytes: 2048 }],
    uptime_seconds: 3660,
  })),
  addMachine: vi.fn(async () => ({})),
  deleteMachine: vi.fn(async () => undefined),
}));

describe('MetricsPage', () => {
  it('shows local monitoring dashboard data', async () => {
    render(<MetricsPage />);
    expect(await screen.findByText('Machine telemetry')).toBeTruthy();
    expect(await screen.findByText('12.5%')).toBeTruthy();
    expect(screen.getByText('Disk usage')).toBeTruthy();
  });
});
