import { apiClient } from '../../shared/api/client';
import { LogSource, LogTail } from './types';

function path(agentId: string, suffix = '') {
  return agentId === 'local' ? `/api/v2/logs${suffix}` : `/api/v2/agents/${encodeURIComponent(agentId)}/logs${suffix}`;
}

export async function listLogs(agentId: string, signal?: AbortSignal): Promise<LogSource[]> {
  return (await apiClient.request<{ items: LogSource[] }>(path(agentId), { signal })).items;
}

export function tailLog(agentId: string, logId: string, signal?: AbortSignal): Promise<LogTail> {
  return apiClient.request<LogTail>(`${path(agentId, `/${encodeURIComponent(logId)}/tail`)}?lines=100`, { signal });
}
