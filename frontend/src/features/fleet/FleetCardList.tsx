import { Link } from 'react-router-dom';
import { Agent } from '../agent-registry/types';

type FleetCardListProps = {
  agents: Agent[];
  onProbe: (agent: Agent) => void;
  onToggle: (agent: Agent) => void;
  onRemove: (agent: Agent) => void;
};

function problems(agent: Agent) {
  const observations = agent.summary?.observations as Record<string, unknown> | undefined;
  const critical = typeof observations?.critical === 'number' ? observations.critical : 0;
  return critical ? `${critical} critical` : agent.workload_status;
}

export function FleetCardList({ agents, onProbe, onToggle, onRemove }: FleetCardListProps) {
  return <ul className="fleet-card-list" aria-label="Fleet agents">
    {agents.map((agent) => <li key={agent.agent_id} className="fleet-card">
      <div><strong>{agent.display_name}</strong><span className={`status status-${agent.connection_status}`}><span aria-hidden="true">●</span>{agent.connection_status}</span></div>
      <p>{problems(agent)}</p>
      <Link className="secondary-button" to={`/agents/${encodeURIComponent(agent.agent_id)}/overview`}>Open {agent.display_name}</Link>
      <div className="dialog-actions"><button type="button" className="secondary-button" onClick={() => onProbe(agent)}>Probe</button><button type="button" className="secondary-button" onClick={() => onToggle(agent)}>{agent.enabled ? 'Disable' : 'Enable'}</button><Link className="secondary-button" to={`/agents/${encodeURIComponent(agent.agent_id)}/settings`}>Edit</Link><button type="button" className="danger-button" onClick={() => onRemove(agent)}>Remove</button></div>
    </li>)}
  </ul>;
}
