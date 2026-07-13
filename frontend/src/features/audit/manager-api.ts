import { apiClient } from '../../shared/api/client';

export type ControlPlaneAuditEvent = {
  id: number;
  timestamp: string;
  actor_id: string;
  source_addr: string | null;
  agent_id: string | null;
  operation: string;
  target: string | null;
  result: string;
  dispatch_state: string | null;
  upstream_status: number | null;
  correlation_id: string;
  failure_category: string | null;
};

export type ControlPlaneAuditFilters = {
  agentId?: string;
  operation?: string;
  result?: string;
  correlationId?: string;
};

export function listControlPlaneAuditEvents(filters: ControlPlaneAuditFilters, signal?: AbortSignal): Promise<{ events: ControlPlaneAuditEvent[] }> {
  const params = new URLSearchParams({ limit: '100' });
  if (filters.agentId) params.set('agent_id', filters.agentId);
  if (filters.operation) params.set('operation', filters.operation);
  if (filters.result) params.set('result', filters.result);
  if (filters.correlationId) params.set('correlation_id', filters.correlationId);
  return apiClient.request(`/api/control-plane/audit?${params}`, { signal });
}
