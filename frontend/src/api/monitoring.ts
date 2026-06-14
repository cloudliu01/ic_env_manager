import { apiClient } from './client';

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

export async function getAgentMonitoringSnapshot(
  agentId: string,
  init: RequestInit = {},
): Promise<HostSnapshot> {
  return apiClient.request<HostSnapshot>(`/api/agents/${encodeURIComponent(agentId)}/monitoring/snapshot`, init);
}
