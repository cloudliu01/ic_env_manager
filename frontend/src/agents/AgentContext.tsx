import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from 'react';
import { AgentSummary, listAgents } from '../api/agents';
import { ApiClientError } from '../api/client';
import { FleetHost, FleetOverview, getFleetOverview, setAgentEnabled } from '../api/fleet';

const ACTIVE_AGENT_STORAGE_KEY = 'activeAgentId';

export function clearActiveAgentSelection(): void {
  window.sessionStorage.removeItem(ACTIVE_AGENT_STORAGE_KEY);
}

type AgentContextValue = {
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
  const [fleet, setFleet] = useState<FleetOverview | null>(null);
  const [activeAgentId, setActiveAgentIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fleetLoading, setFleetLoading] = useState(true);
  const [fleetError, setFleetError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    getFleetOverview({ signal: controller.signal })
      .then((overview) => {
        if (!active) {
          return;
        }
        const items = overview.hosts;
        const nextId = chooseActiveAgentId(items, window.sessionStorage.getItem(ACTIVE_AGENT_STORAGE_KEY));
        setFleet(overview);
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
        setFleetError(err instanceof Error ? err.message : 'Unable to load fleet overview');
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
          .catch((listErr: unknown) => {
            if (!active) {
              return;
            }
            if (listErr instanceof ApiClientError && listErr.status === 401 && onAuthenticationExpired) {
              clearActiveAgentSelection();
              onAuthenticationExpired();
              return;
            }
            setError(listErr instanceof Error ? listErr.message : 'Unable to load agents');
            setActiveAgentIdState(null);
            clearActiveAgentSelection();
          })
          .finally(() => {
            if (active) {
              setLoading(false);
              setFleetLoading(false);
            }
          });
      })
      .finally(() => {
        if (active) {
          setLoading(false);
          setFleetLoading(false);
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [onAuthenticationExpired]);

  async function refreshFleet() {
    setFleetLoading(true);
    setFleetError(null);
    try {
      const overview = await getFleetOverview();
      const nextId = chooseActiveAgentId(overview.hosts, activeAgentId);
      setFleet(overview);
      setAgents(overview.hosts);
      setActiveAgentIdState(nextId);
      if (nextId) {
        window.sessionStorage.setItem(ACTIVE_AGENT_STORAGE_KEY, nextId);
      } else {
        clearActiveAgentSelection();
      }
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 401 && onAuthenticationExpired) {
        clearActiveAgentSelection();
        onAuthenticationExpired();
        return;
      }
      setFleetError(err instanceof Error ? err.message : 'Unable to load fleet overview');
    } finally {
      setFleetLoading(false);
    }
  }

  async function setHostEnabled(agentId: string, enabled: boolean) {
    const updated = await setAgentEnabled(agentId, enabled);
    setFleet((current) => current ? {
      ...current,
      hosts: current.hosts.map((host) => (host.id === agentId ? { ...host, ...updated } : host)),
    } : current);
    setAgents((current) => current.map((agent) => (agent.id === agentId ? { ...agent, ...updated } : agent)));
    if (!enabled && activeAgentId === agentId) {
      const candidates = agents.map((agent) => (agent.id === agentId ? { ...agent, ...updated } : agent));
      setActiveAgentIdState(chooseActiveAgentId(candidates, null));
    }
    await refreshFleet();
  }

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
    () => fleet?.hosts.find((agent) => agent.id === activeAgentId) ?? agents.find((agent) => agent.id === activeAgentId) ?? null,
    [fleet, agents, activeAgentId],
  );

  const fleetHosts = useMemo(() => fleet?.hosts ?? agents, [fleet, agents]);

  const value = useMemo(
    () => ({ agents, activeAgentId, activeAgent, loading, error, setActiveAgentId, fleet, fleetHosts, fleetLoading, fleetError, refreshFleet, setHostEnabled }),
    [agents, activeAgentId, activeAgent, loading, error, fleet, fleetHosts, fleetLoading, fleetError],
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
