export type AuditTarget = { agentId: string; name: string; capabilities: string[] };
export type AuditFilters = { operation?: string; result?: string; cursor?: string; limit?: number };
export type AuditEvent = { id: number; timestamp: string; agent_id?: string | null; operation: string; target_type: string; result: string; failure_reason?: string | null };
export type AuditEventsResponse = { events: AuditEvent[]; next_cursor?: string | null };
