import { apiClient } from './client';

export type MachineSummary = {
  id: string;
  name: string;
  address: string;
  port: number | null;
  endpoint: string;
  is_local: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type MachineCreateRequest = {
  name?: string;
  address: string;
  port: number;
  key: string;
};

export type MetricBytes = {
  used_bytes: number;
  total_bytes: number;
  available_bytes?: number;
  free_bytes?: number;
  percent: number;
};

export type CpuSnapshot = {
  percent: number;
  cores_logical: number;
  cores_physical: number;
  load_average: number[];
};

export type DiskSnapshot = MetricBytes & {
  mount: string;
  device?: string;
  fstype?: string;
};

export type NetworkSnapshot = {
  interface: string;
  rx_bytes: number;
  tx_bytes: number;
  rx_packets?: number;
  tx_packets?: number;
};

export type HostSnapshot = {
  host_id: string;
  name: string;
  address: string;
  hostname?: string | null;
  status: 'online' | 'offline' | string;
  sampled_at: string;
  error?: string;
  cpu: CpuSnapshot;
  memory: MetricBytes;
  swap: MetricBytes;
  disks: DiskSnapshot[];
  network: NetworkSnapshot[];
  uptime_seconds: number;
};

export async function listMachines(): Promise<MachineSummary[]> {
  const response = await apiClient.request<{ machines: MachineSummary[] }>('/api/monitoring/machines');
  return response.machines;
}

export async function addMachine(machine: MachineCreateRequest): Promise<MachineSummary> {
  return apiClient.request<MachineSummary>('/api/monitoring/machines', {
    method: 'POST',
    body: JSON.stringify(machine),
  });
}

export async function deleteMachine(machineId: string): Promise<void> {
  await apiClient.request<void>(`/api/monitoring/machines/${machineId}`, { method: 'DELETE' });
}

export async function getMachineSnapshot(machineId: string): Promise<HostSnapshot> {
  return apiClient.request<HostSnapshot>(`/api/monitoring/machines/${machineId}/snapshot`);
}
