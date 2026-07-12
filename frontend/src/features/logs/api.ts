import { apiClient } from '../../shared/api/client';

export type LogSource = {
  id: string;
  path: string;
  last_updated: string;
  observed_at: string;
  ttl_seconds: number;
  received_at: string;
  expires_at: string;
  producer_id: string;
  updated_at: string;
  stale: boolean;
};

export type LogTail = {
  id: string;
  path: string;
  lines: string[];
  line_count: number;
  truncated: boolean;
  last_updated: string;
};

export async function listLogs(signal?: AbortSignal): Promise<LogSource[]> {
  return (await apiClient.request<{ items: LogSource[] }>('/api/v2/logs', { signal })).items;
}

export function tailLog(logId: string, signal?: AbortSignal): Promise<LogTail> {
  return apiClient.request<LogTail>(`/api/v2/logs/${encodeURIComponent(logId)}/tail?lines=100`, { signal });
}
