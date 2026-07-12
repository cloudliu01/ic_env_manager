import { apiClient } from '../../shared/api/client';
import { AuditEventsResponse, AuditFilters } from './types';

export function listAuditEvents(agentId: string, filters: AuditFilters = {}, signal?: AbortSignal): Promise<AuditEventsResponse> {
  const params = new URLSearchParams({ limit: String(filters.limit ?? 100) });
  if (filters.operation) params.set('operation', filters.operation);
  if (filters.result) params.set('result', filters.result);
  if (filters.cursor) params.set('cursor', filters.cursor);
  const base = agentId === 'local' ? '/api/audit' : `/api/agents/${encodeURIComponent(agentId)}/audit`;
  return apiClient.request(`${base}?${params}`, { signal });
}
