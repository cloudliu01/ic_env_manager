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

export async function listTerminals(): Promise<TerminalSession[]> {
  const response = await apiClient.request<{ terminals: TerminalSession[] }>('/api/terminals');
  return response.terminals;
}

export async function createTerminal(title = 'Terminal'): Promise<TerminalSession> {
  return apiClient.request<TerminalSession>('/api/terminals', {
    method: 'POST',
    body: JSON.stringify({ title, rows: 24, cols: 80 }),
  });
}

export async function closeTerminal(id: string): Promise<TerminalSession> {
  return apiClient.request<TerminalSession>(`/api/terminals/${id}`, { method: 'DELETE' });
}

export async function resizeTerminal(id: string, rows: number, cols: number): Promise<void> {
  await apiClient.request<void>(`/api/terminals/${id}/resize`, {
    method: 'POST',
    body: JSON.stringify({ rows, cols }),
  });
}

export async function getTerminalHistory(id: string, cursor: number): Promise<TerminalHistory> {
  return apiClient.request<TerminalHistory>(`/api/terminals/${id}/history?cursor=${cursor}`);
}

export async function createConnectToken(id: string): Promise<{ ticket: string; expires_in_seconds: number }> {
  return apiClient.request<{ ticket: string; expires_in_seconds: number }>(
    `/api/terminals/${id}/connect-token`,
    { method: 'POST' },
  );
}
