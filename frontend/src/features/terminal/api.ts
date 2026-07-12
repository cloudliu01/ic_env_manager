import { apiClient } from '../../shared/api/client';
import { TerminalSession } from './types';

function path(agentId: string, suffix = '') {
  return agentId === 'local' ? `/api/terminals${suffix}` : `/api/agents/${encodeURIComponent(agentId)}/terminals${suffix}`;
}

export async function listTerminals(agentId: string, init: RequestInit = {}): Promise<TerminalSession[]> {
  return (await apiClient.request<{ terminals: TerminalSession[] }>(path(agentId), init)).terminals;
}

export function createTerminal(agentId: string, title: string): Promise<TerminalSession> {
  return apiClient.request(path(agentId), { method: 'POST', body: JSON.stringify({ title, rows: 24, cols: 80 }) });
}

export function closeTerminal(agentId: string, id: string): Promise<TerminalSession> {
  return apiClient.request(path(agentId, `/${encodeURIComponent(id)}`), { method: 'DELETE' });
}

export async function resizeTerminal(agentId: string, id: string, rows: number, cols: number): Promise<void> {
  await apiClient.request(path(agentId, `/${encodeURIComponent(id)}/resize`), { method: 'POST', body: JSON.stringify({ rows, cols }) });
}

export function createConnectToken(agentId: string, id: string): Promise<{ ticket: string; expires_in_seconds: number }> {
  return apiClient.request(path(agentId, `/${encodeURIComponent(id)}/connect-token`), { method: 'POST' });
}
