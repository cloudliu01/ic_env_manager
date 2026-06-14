import { apiClient } from './client';

export type AuditEvent = {
  id: number;
  timestamp: string;
  agent_id?: string | null;
  actor_id?: string | null;
  source_addr?: string | null;
  correlation_id?: string | null;
  operation: string;
  target_type: string;
  target_id?: string | null;
  result: string;
  failure_reason?: string | null;
};

export type AuditEventsResponse = {
  events: AuditEvent[];
};

export async function listGatewayAuditEvents(limit = 100): Promise<AuditEventsResponse> {
  return apiClient.request<AuditEventsResponse>(`/api/control-plane/audit?limit=${limit}`);
}

export async function listAgentAuditEvents(
  agentId: string,
  limit = 100,
): Promise<AuditEventsResponse> {
  return apiClient.request<AuditEventsResponse>(
    `/api/agents/${encodeURIComponent(agentId)}/audit?limit=${limit}`,
  );
}
