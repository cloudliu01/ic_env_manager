import { apiClient } from '../../shared/api/client';
import { ServiceSummary } from './types';

function path(agentId: string, suffix = '') {
  return agentId === 'local' ? `/api/services${suffix}` : `/api/agents/${encodeURIComponent(agentId)}/services${suffix}`;
}

export async function listServices(agentId: string, init: RequestInit = {}): Promise<ServiceSummary[]> {
  return (await apiClient.request<{ services: ServiceSummary[] }>(path(agentId), init)).services;
}

export function startService(agentId: string, id: string) {
  return apiClient.request(path(agentId, `/${encodeURIComponent(id)}/start`), { method: 'POST' });
}

export function stopService(agentId: string, id: string) {
  return apiClient.request(path(agentId, `/${encodeURIComponent(id)}/stop`), { method: 'POST' });
}
