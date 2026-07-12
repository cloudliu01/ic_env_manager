export type LogsTarget = { agentId: string; name: string; capabilities: string[] };
export type LogSource = { id: string; path: string; last_updated: string; observed_at: string; ttl_seconds: number; received_at: string; expires_at: string; producer_id: string; updated_at: string; stale: boolean };
export type LogTail = { id: string; path: string; lines: string[]; line_count: number; truncated: boolean; last_updated: string };
