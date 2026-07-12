import { ReactNode } from 'react';
import type { AgentSummary } from '../api/agents';
import { AgentContext, AgentContextValue } from './AgentStateContext';

export function StandaloneAgentProvider({
  children,
  name,
  capabilities,
}: {
  children: ReactNode;
  name: string;
  capabilities: string[];
}) {
  const agent: AgentSummary = { id: 'local', name, status: 'ready', enabled: true, capabilities };
  const value: AgentContextValue = {
    agents: [agent],
    activeAgentId: 'local',
    activeAgent: agent,
    loading: false,
    error: null,
    setActiveAgentId: () => undefined,
    fleet: null,
    fleetHosts: [agent],
    fleetLoading: false,
    fleetError: null,
    refreshFleet: async () => undefined,
    setHostEnabled: async () => undefined,
  };
  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>;
}
