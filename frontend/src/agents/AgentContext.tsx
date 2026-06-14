import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';
import { AgentSummary, listAgents } from '../api/agents';
import { ApiClientError } from '../api/client';

const ACTIVE_AGENT_STORAGE_KEY = 'activeAgentId';

export function clearActiveAgentSelection(): void {
  window.sessionStorage.removeItem(ACTIVE_AGENT_STORAGE_KEY);
}

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

export function AgentProvider({ children, onAuthenticationExpired }: { children: ReactNode; onAuthenticationExpired?: () => void }) {
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
          clearActiveAgentSelection();
        }
      })
      .catch((err: unknown) => {
        if (!active) {
          return;
        }
        if (err instanceof ApiClientError && err.status === 401 && onAuthenticationExpired) {
          clearActiveAgentSelection();
          onAuthenticationExpired();
          return;
        }
        setError(err instanceof Error ? err.message : 'Unable to load agents');
        setActiveAgentIdState(null);
        clearActiveAgentSelection();
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [onAuthenticationExpired]);

  function setActiveAgentId(agentId: string) {
    const nextId = agents.some((agent) => agent.id === agentId) ? agentId : null;
    setActiveAgentIdState(nextId);
    if (nextId) {
      window.sessionStorage.setItem(ACTIVE_AGENT_STORAGE_KEY, nextId);
    } else {
      clearActiveAgentSelection();
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
