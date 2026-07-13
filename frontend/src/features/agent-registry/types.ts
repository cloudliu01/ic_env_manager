export type AgentCapability = string;

export type Agent = {
  agent_id: string;
  instance_id?: string | null;
  display_name: string;
  endpoint?: string | null;
  enabled: boolean;
  revision?: number;
  transport_profile_id?: string | null;
  transport_warning?: string | null;
  connection_status: string;
  workload_status: string;
  observed_at?: string | null;
  stale_after?: string | null;
  api_version?: string | null;
  agent_version?: string | null;
  capabilities: AgentCapability[];
  summary?: Record<string, unknown>;
  last_error_code?: string | null;
};

export type AgentFilters = {
  query?: string;
  connection_status?: string;
  workload_status?: string;
  capability?: string;
  problem?: string;
};

export type ObservationFilters = {
  status?: string;
};

export type AgentObservation = {
  identity_key?: string;
  namespace?: string;
  name: string;
  status?: string;
  message?: string | null;
  observed_at?: string;
};
