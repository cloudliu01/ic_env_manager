import { useRouteAgent } from './AgentLayout';

function count(summary: Record<string, unknown> | undefined, section: string, key: string) {
  const value = summary?.[section];
  return typeof value === 'object' && value && typeof (value as Record<string, unknown>)[key] === 'number'
    ? (value as Record<string, number>)[key] : 0;
}

function statusLabel(value: string) {
  return `${value[0].toUpperCase()}${value.slice(1)}`;
}

export function AgentOverviewPage() {
  const { agent } = useRouteAgent();
  return <section className="agent-overview"><h1 tabIndex={-1}>{agent.display_name}</h1>
    <div className="overview-grid"><section className="detail-panel"><h2>Connection</h2><p className={`status status-${agent.connection_status}`}>{statusLabel(agent.connection_status)}</p><p>Last probe: {agent.observed_at ? new Date(agent.observed_at).toLocaleString() : 'Never'}</p></section>
      <section className="detail-panel"><h2>Workload</h2><p className={`status status-${agent.workload_status}`}>{statusLabel(agent.workload_status)}</p><p>{count(agent.summary, 'observations', 'critical')} critical</p><p>{count(agent.summary, 'services', 'unhealthy')} unhealthy</p></section></div>
    {agent.last_error_code ? <p className="truncation-notice" role="alert">Last known error: {agent.last_error_code}</p> : null}
  </section>;
}
