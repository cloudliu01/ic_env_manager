import { apiClient } from '../../shared/api/client';
import { Agent, AgentFilters, AgentObservation, ObservationFilters } from './types';

function queryString(filters: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `?${query}` : '';
}

export async function listAgents(filters: AgentFilters, signal?: AbortSignal): Promise<Agent[]> {
  const response = await apiClient.request<{ agents: Agent[] }>(`/api/v2/agents${queryString(filters)}`, { signal });
  return response.agents;
}

export async function getAgent(agentId: string, signal?: AbortSignal): Promise<Agent> {
  const response = await apiClient.request<{ agent: Agent }>(`/api/v2/agents/${encodeURIComponent(agentId)}`, { signal });
  return response.agent;
}

export async function listAgentObservations(
  agentId: string,
  filters: ObservationFilters,
  signal?: AbortSignal,
): Promise<AgentObservation[]> {
  const response = await apiClient.request<{ items: AgentObservation[] }>(
    `/api/v2/agents/${encodeURIComponent(agentId)}/observations${queryString(filters)}`,
    { signal },
  );
  return response.items;
}
