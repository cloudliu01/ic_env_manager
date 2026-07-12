import { PropsWithChildren } from 'react';
import { Link } from 'react-router-dom';

type CapabilityRouteProps = PropsWithChildren<{
  agentId: string;
  capability: string;
  capabilities: string[];
}>;

export function CapabilityRoute({ agentId, capability, capabilities, children }: CapabilityRouteProps) {
  if (capabilities.includes(capability)) return children;

  return (
    <section className="feature-page">
      <h1 tabIndex={-1}>Feature unavailable</h1>
      <p role="alert">This Agent does not advertise <code>{capability}</code>.</p>
      <Link to={`/agents/${encodeURIComponent(agentId)}/overview`}>Return to agent overview</Link>
    </section>
  );
}
