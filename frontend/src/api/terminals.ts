import { apiClient } from './client';

export type TerminalSession = {
  id: string;
  owner: string;
  title: string;
  pid: number | null;
  rows: number;
  cols: number;
  status: string;
  output_cursor: number;
  replay_buffer_start_cursor: number;
  idle_timeout_minutes: number;
  created_at: string;
  last_active_at: string;
  exited_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
};

export type TerminalHistory = {
  terminal_id: string;
  from_cursor: number;
  to_cursor: number;
  buffer_start_cursor: number;
  truncated: boolean;
  status: string;
  output: string;
};

function terminalPath(agentId: string, path = ''): string {
  if (agentId === 'local') {
    return `/api/terminals${path}`;
  }
  return `/api/agents/${encodeURIComponent(agentId)}/terminals${path}`;
}

export async function listTerminals(agentId: string, init?: RequestInit): Promise<TerminalSession[]> {
  const path = terminalPath(agentId);
  const response = init
    ? await apiClient.request<{ terminals: TerminalSession[] }>(path, init)
    : await apiClient.request<{ terminals: TerminalSession[] }>(path);
  return response.terminals;
}

export async function createTerminal(agentId: string, title = 'Terminal'): Promise<TerminalSession> {
  return apiClient.request<TerminalSession>(terminalPath(agentId), {
    method: 'POST',
    body: JSON.stringify({ title, rows: 24, cols: 80 }),
  });
}

export async function closeTerminal(agentId: string, id: string): Promise<TerminalSession> {
  return apiClient.request<TerminalSession>(terminalPath(agentId, `/${encodeURIComponent(id)}`), { method: 'DELETE' });
}

export async function resizeTerminal(agentId: string, id: string, rows: number, cols: number): Promise<void> {
  await apiClient.request<void>(terminalPath(agentId, `/${encodeURIComponent(id)}/resize`), {
    method: 'POST',
    body: JSON.stringify({ rows, cols }),
  });
}

export async function getTerminalHistory(agentId: string, id: string, cursor: number): Promise<TerminalHistory> {
  return apiClient.request<TerminalHistory>(
    terminalPath(agentId, `/${encodeURIComponent(id)}/history?cursor=${encodeURIComponent(String(cursor))}`),
  );
}

export async function createConnectToken(
  agentId: string,
  id: string,
): Promise<{ ticket: string; expires_in_seconds: number }> {
  return apiClient.request<{ ticket: string; expires_in_seconds: number }>(
    terminalPath(agentId, `/${encodeURIComponent(id)}/connect-token`),
    { method: 'POST' },
  );
}
