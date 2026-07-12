import { useQuery } from '@tanstack/react-query';
import { getAgent, listAgentObservations, listAgents } from './api';
import { AgentFilters, ObservationFilters } from './types';

export const agentKeys = {
  all: ['agents'] as const,
  list: (filters: AgentFilters) => ['agents', 'list', filters] as const,
  detail: (agentId: string) => ['agents', 'detail', agentId] as const,
  observations: (agentId: string, filters: ObservationFilters) =>
    ['agents', agentId, 'observations', filters] as const,
};

function pollingInterval() {
  return document.visibilityState === 'visible' ? 30_000 : false;
}

export function useAgents(filters: AgentFilters) {
  return useQuery({
    queryKey: agentKeys.list(filters),
    queryFn: ({ signal }) => listAgents(filters, signal),
    refetchInterval: pollingInterval,
    refetchIntervalInBackground: false,
  });
}

export function useAgent(agentId: string) {
  return useQuery({
    queryKey: agentKeys.detail(agentId),
    queryFn: ({ signal }) => getAgent(agentId, signal),
    enabled: Boolean(agentId),
  });
}

export function useAgentObservations(agentId: string, filters: ObservationFilters) {
  return useQuery({
    queryKey: agentKeys.observations(agentId, filters),
    queryFn: ({ signal }) => listAgentObservations(agentId, filters, signal),
    enabled: Boolean(agentId),
    refetchInterval: pollingInterval,
    refetchIntervalInBackground: false,
  });
}
