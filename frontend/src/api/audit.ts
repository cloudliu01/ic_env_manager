import { apiClient } from './client';

export type AuditEvent = {
  id: number;
  timestamp: string;
  actor_id?: string | null;
  source_addr?: string | null;
  operation: string;
  target_type: string;
  target_id?: string | null;
  result: string;
  failure_reason?: string | null;
};

export type AuditEventsResponse = {
  events: AuditEvent[];
};

export async function listAuditEvents(limit = 100): Promise<AuditEventsResponse> {
  return apiClient.request<AuditEventsResponse>(`/api/audit?limit=${limit}`);
}
