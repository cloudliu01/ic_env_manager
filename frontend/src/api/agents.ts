import { apiClient } from './client';

export type AgentSummary = {
  id: string;
  name: string;
  status: string;
  enabled: boolean;
  capabilities?: string[];
};

export async function listAgents(): Promise<AgentSummary[]> {
  const response = await apiClient.request<{ agents: AgentSummary[] }>('/api/agents');
  return response.agents;
}
