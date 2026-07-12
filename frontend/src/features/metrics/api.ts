import { apiClient } from '../../shared/api/client';
import { HostSnapshot } from './types';

export function getMonitoringSnapshot(agentId: string, init: RequestInit = {}): Promise<HostSnapshot> {
  const path = agentId === 'local' ? '/api/monitoring/local' : `/api/agents/${encodeURIComponent(agentId)}/monitoring/snapshot`;
  return apiClient.request(path, init);
}
