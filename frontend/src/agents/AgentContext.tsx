import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';
import { AgentSummary, listAgents } from '../api/agents';

const ACTIVE_AGENT_STORAGE_KEY = 'activeAgentId';

type AgentContextValue = {
  agents: AgentSummary[];
  activeAgentId: string | null;
  activeAgent: AgentSummary | null;
  loading: boolean;
  error: string | null;
  setActiveAgentId: (agentId: string) => void;
};

const AgentContext = createContext<AgentContextValue | null>(null);

export function agentSupports(agent: AgentSummary | null, capability: string): boolean {
  return Boolean(agent?.enabled && agent.status === 'ready' && (agent.capabilities ?? []).includes(capability));
}

function chooseActiveAgentId(agents: AgentSummary[], storedId: string | null): string | null {
  if (storedId && agents.some((agent) => agent.id === storedId)) {
    return storedId;
  }

  return agents.find((agent) => agent.enabled && agent.status === 'ready')?.id
    ?? agents.find((agent) => agent.enabled)?.id
    ?? null;
}

export function AgentProvider({ children }: { children: ReactNode }) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [activeAgentId, setActiveAgentIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    listAgents()
      .then((items) => {
        if (!active) {
          return;
        }
        const nextId = chooseActiveAgentId(items, window.sessionStorage.getItem(ACTIVE_AGENT_STORAGE_KEY));
        setAgents(items);
        setActiveAgentIdState(nextId);
        if (nextId) {
          window.sessionStorage.setItem(ACTIVE_AGENT_STORAGE_KEY, nextId);
        } else {
          window.sessionStorage.removeItem(ACTIVE_AGENT_STORAGE_KEY);
        }
      })
      .catch((err: Error) => {
        if (!active) {
          return;
        }
        setError(err.message);
        setActiveAgentIdState(null);
        window.sessionStorage.removeItem(ACTIVE_AGENT_STORAGE_KEY);
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  function setActiveAgentId(agentId: string) {
    const nextId = agents.some((agent) => agent.id === agentId) ? agentId : null;
    setActiveAgentIdState(nextId);
    if (nextId) {
      window.sessionStorage.setItem(ACTIVE_AGENT_STORAGE_KEY, nextId);
    } else {
      window.sessionStorage.removeItem(ACTIVE_AGENT_STORAGE_KEY);
    }
  }

  const activeAgent = useMemo(
    () => agents.find((agent) => agent.id === activeAgentId) ?? null,
    [agents, activeAgentId],
  );

  const value = useMemo(
    () => ({ agents, activeAgentId, activeAgent, loading, error, setActiveAgentId }),
    [agents, activeAgentId, activeAgent, loading, error],
  );

  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>;
}

export function useActiveAgent() {
  const value = useContext(AgentContext);
  if (!value) {
    throw new Error('useActiveAgent must be used within AgentProvider');
  }
  return value;
}
