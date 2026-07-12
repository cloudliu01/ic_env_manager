export type AgentTarget = {
  agentId: string;
  name: string;
  capabilities: string[];
};

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
