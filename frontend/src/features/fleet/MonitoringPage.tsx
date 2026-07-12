import { Link } from 'react-router-dom';
import { Agent } from '../agent-registry/types';
import { useFleetOverview } from './queries';

function count(agent: Agent, section: string, key: string) {
  const value = agent.summary?.[section];
  return typeof value === 'object' && value && typeof (value as Record<string, unknown>)[key] === 'number'
    ? (value as Record<string, number>)[key] : 0;
}

function hasProblem(agent: Agent) {
  return agent.connection_status !== 'ready' || !['healthy', 'unknown'].includes(agent.workload_status) || Boolean(agent.last_error_code);
}

export function MonitoringPage() {
  const fleet = useFleetOverview();
  const problems = (fleet.data?.agents ?? []).filter(hasProblem);
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Monitoring</h1><p>Cached Fleet health. No live Agent telemetry is requested here.</p></header>
    {fleet.isPending ? <p role="status" aria-live="polite">Loading monitoring…</p> : null}
    {fleet.isError ? <p role="alert">Monitoring data could not be loaded.</p> : null}
    {fleet.data ? <section className="detail-panel"><h2>Problems ({problems.length})</h2>
      {problems.length === 0 ? <p>No cached Fleet problems.</p> : <ul className="monitoring-list">{problems.map((agent) => <li key={agent.agent_id}>
        <strong>{agent.display_name}</strong><span className="status status-warning">{agent.connection_status}</span><span className="status status-warning">{agent.workload_status}</span>
        <span>{count(agent, 'observations', 'critical')} critical</span><span>{count(agent, 'observations', 'stale')} stale</span><span>{count(agent, 'services', 'unhealthy')} unhealthy</span>
        {agent.last_error_code ? <span>Last error: {agent.last_error_code}</span> : null}
        <Link to={`/agents/${encodeURIComponent(agent.agent_id)}/overview`}>Open {agent.display_name} overview</Link>
      </li>)}</ul>}
    </section> : null}
  </section>;
}
