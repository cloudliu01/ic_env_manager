import { apiClient } from './client';

export type ServiceSummary = {
  id: string;
  name: string;
  status: string;
  health_status: string;
  allowed_operations: string[];
};

function agentPath(agentId: string, path: string): string {
  if (agentId === 'local') {
    return `/api${path}`;
  }
  return `/api/agents/${encodeURIComponent(agentId)}${path}`;
}

export async function listServices(agentId: string, init?: RequestInit): Promise<ServiceSummary[]> {
  const path = agentPath(agentId, '/services');
  const response = init
    ? await apiClient.request<{ services: ServiceSummary[] }>(path, init)
    : await apiClient.request<{ services: ServiceSummary[] }>(path);
  return response.services;
}

export async function startService(agentId: string, id: string) {
  return apiClient.request(agentPath(agentId, `/services/${encodeURIComponent(id)}/start`), { method: 'POST' });
}

export async function stopService(agentId: string, id: string) {
  return apiClient.request(agentPath(agentId, `/services/${encodeURIComponent(id)}/stop`), { method: 'POST' });
}
