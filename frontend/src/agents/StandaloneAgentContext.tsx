import { createContext, PropsWithChildren, useContext } from 'react';

export type StandaloneAgentIdentity = {
  agentId: string;
  name: string;
  capabilities: string[];
};

const StandaloneAgentContext = createContext<StandaloneAgentIdentity | null>(null);

export function StandaloneAgentIdentityProvider({ children, value }: PropsWithChildren<{ value: StandaloneAgentIdentity }>) {
  return <StandaloneAgentContext.Provider value={value}>{children}</StandaloneAgentContext.Provider>;
}

export function useStandaloneAgent(): StandaloneAgentIdentity {
  const identity = useContext(StandaloneAgentContext);
  if (!identity) throw new Error('useStandaloneAgent must be used within StandaloneAgentProvider');
  return identity;
}

export function supportsCapability(capabilities: string[], capability: string): boolean {
  return capabilities.includes(capability);
}
