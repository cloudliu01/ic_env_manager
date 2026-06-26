import { apiClient } from './client';

export type HostStatus = 'ready' | 'degraded' | 'unavailable' | 'disabled' | 'unknown' | string;

export type FleetHost = {
  id: string;
  name: string;
  enabled: boolean;
  status: HostStatus;
  observed_at?: string | null;
  stale_after?: string | null;
  api_version?: string | null;
  agent_version?: string | null;
  capabilities?: string[];
  last_error?: string | null;
  summary?: {
    cpu_percent?: number;
    mem_percent?: number;
    load1?: number;
    service_count?: number;
  };
};

export type FleetOverview = {
  hosts: FleetHost[];
  collected_at: string;
};

export async function getFleetOverview(init: RequestInit = {}): Promise<FleetOverview> {
  return apiClient.request<FleetOverview>('/api/fleet/overview', init);
}

export async function setAgentEnabled(agentId: string, enabled: boolean): Promise<FleetHost> {
  const response = await apiClient.request<{ agent: FleetHost }>(`/api/agents/${encodeURIComponent(agentId)}/enabled`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });
  return response.agent;
}
