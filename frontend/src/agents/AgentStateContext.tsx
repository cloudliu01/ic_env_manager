import { createContext, useContext } from 'react';
import type { AgentSummary } from '../api/agents';
import type { FleetHost, FleetOverview } from '../api/fleet';

export type AgentContextValue = {
  agents: AgentSummary[];
  activeAgentId: string | null;
  activeAgent: FleetHost | AgentSummary | null;
  loading: boolean;
  error: string | null;
  setActiveAgentId: (agentId: string) => void;
  fleet: FleetOverview | null;
  fleetHosts: FleetHost[];
  fleetLoading: boolean;
  fleetError: string | null;
  refreshFleet: () => Promise<void>;
  setHostEnabled: (agentId: string, enabled: boolean) => Promise<void>;
};

export const AgentContext = createContext<AgentContextValue | null>(null);

export function agentSupports(agent: AgentSummary | null, capability: string): boolean {
  return Boolean(agent?.enabled && agent.status === 'ready' && (agent.capabilities ?? []).includes(capability));
}

export function useActiveAgent() {
  const value = useContext(AgentContext);
  if (!value) {
    throw new Error('useActiveAgent must be used within AgentProvider');
  }
  return value;
}
