import { ReactNode } from 'react';
import { StandaloneAgentIdentityProvider } from './StandaloneAgentContext';

export function StandaloneAgentProvider({
  children,
  name,
  capabilities,
}: {
  children: ReactNode;
  name: string;
  capabilities: string[];
}) {
  return <StandaloneAgentIdentityProvider value={{ agentId: 'local', name, capabilities }}>{children}</StandaloneAgentIdentityProvider>;
}
