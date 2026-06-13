import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MetricsPage } from '../src/pages/MetricsPage';
import { addMachine, deleteMachine, getMachineSnapshot, listMachines } from '../src/api/monitoring';

vi.mock('../src/api/monitoring', () => ({
  listMachines: vi.fn(async () => [
    { id: 'local', name: 'Local host', address: '127.0.0.1', port: null, endpoint: 'local', is_local: true, created_at: null, updated_at: null },
  ]),
  getMachineSnapshot: vi.fn(async (id: string) => ({
    host_id: id,
    name: id === 'remote-1' ? 'Remote host' : 'Local host',
    address: id === 'remote-1' ? 'http://10.0.0.2:8765' : '127.0.0.1',
    hostname: id === 'remote-1' ? 'remote' : 'local',
    status: id === 'offline-1' ? 'offline' : 'online',
    sampled_at: '2026-06-13T00:00:00Z',
    error: id === 'offline-1' ? 'connection refused' : undefined,
    cpu: { percent: id === 'remote-1' ? 55.5 : 12.5, cores_logical: 8, cores_physical: 4, load_average: [1.1, 1.2, 1.3] },
    memory: { used_bytes: 4 * 1024 ** 3, total_bytes: 16 * 1024 ** 3, available_bytes: 12 * 1024 ** 3, percent: 25 },
    swap: { used_bytes: 0, total_bytes: 0, free_bytes: 0, percent: 0 },
    disks: [{ mount: '/', device: '/dev/disk1', fstype: 'apfs', used_bytes: 100 * 1024 ** 3, total_bytes: 500 * 1024 ** 3, free_bytes: 400 * 1024 ** 3, percent: 20 }],
    network: [{ interface: 'en0', rx_bytes: 1024, tx_bytes: 2048 }],
    uptime_seconds: 3660,
  })),
  addMachine: vi.fn(async () => ({ id: 'remote-1', name: 'Remote host', address: '10.0.0.2', port: 8765, endpoint: 'http://10.0.0.2:8765', is_local: false, created_at: '2026-06-13T00:00:00Z', updated_at: '2026-06-13T00:00:00Z' })),
  deleteMachine: vi.fn(async () => undefined),
}));

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

  it('adds a remote machine and selects it', async () => {
    const user = userEvent.setup();
    vi.mocked(listMachines)
      .mockResolvedValueOnce([
        { id: 'local', name: 'Local host', address: '127.0.0.1', port: null, endpoint: 'local', is_local: true, created_at: null, updated_at: null },
      ])
      .mockResolvedValueOnce([
        { id: 'local', name: 'Local host', address: '127.0.0.1', port: null, endpoint: 'local', is_local: true, created_at: null, updated_at: null },
        { id: 'remote-1', name: 'Remote host', address: '10.0.0.2', port: 8765, endpoint: 'http://10.0.0.2:8765', is_local: false, created_at: '2026-06-13T00:00:00Z', updated_at: '2026-06-13T00:00:00Z' },
      ]);

    render(<MetricsPage />);

    await user.type(await screen.findByPlaceholderText('Lab workstation'), 'Remote host');
    await user.type(screen.getByPlaceholderText('192.168.1.25'), '10.0.0.2');
    await user.clear(screen.getByDisplayValue('8765'));
    await user.type(screen.getByLabelText(/port/i), '8765');
    await user.type(screen.getByPlaceholderText('Bearer token'), 'remote-key');
    await user.click(screen.getByRole('button', { name: /add machine/i }));

    await waitFor(() => expect(addMachine).toHaveBeenCalledWith({ name: 'Remote host', address: '10.0.0.2', port: 8765, key: 'remote-key' }));
    await waitFor(() => expect(getMachineSnapshot).toHaveBeenCalledWith('remote-1'));
  });

  it('deletes a remote machine', async () => {
    const user = userEvent.setup();
    vi.mocked(listMachines).mockResolvedValue([
      { id: 'local', name: 'Local host', address: '127.0.0.1', port: null, endpoint: 'local', is_local: true, created_at: null, updated_at: null },
      { id: 'remote-1', name: 'Remote host', address: '10.0.0.2', port: 8765, endpoint: 'http://10.0.0.2:8765', is_local: false, created_at: '2026-06-13T00:00:00Z', updated_at: '2026-06-13T00:00:00Z' },
    ]);

    render(<MetricsPage />);

    await user.click((await screen.findAllByRole('button', { name: /delete/i }))[1]);

    await waitFor(() => expect(deleteMachine).toHaveBeenCalledWith('remote-1'));
  });
});
