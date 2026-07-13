import { useQuery } from '@tanstack/react-query';
import { ChangeEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ControlPlaneAuditFilters, listControlPlaneAuditEvents } from './manager-api';

export function ManagerAuditPage() {
  const [params, setParams] = useSearchParams();
  const filters: ControlPlaneAuditFilters = {
    agentId: params.get('agent_id') || undefined,
    operation: params.get('operation') || undefined,
    result: params.get('result') || undefined,
    correlationId: params.get('correlation_id') || undefined,
  };
  const audit = useQuery({
    queryKey: ['control-plane-audit', filters],
    queryFn: ({ signal }) => listControlPlaneAuditEvents(filters, signal),
  });

  const updateFilter = (key: string) => (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const next = new URLSearchParams(params);
    if (event.target.value) next.set(key, event.target.value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return <section className="feature-page">
    <div className="page-heading"><div><h1 tabIndex={-1}>Control-plane Audit</h1><p>Manager actions and Agent dispatch outcomes.</p></div></div>
    <div className="fleet-filters" aria-label="Control-plane audit filters">
      <label>Agent filter<input value={filters.agentId ?? ''} onChange={updateFilter('agent_id')} /></label>
      <label>Operation filter<input value={filters.operation ?? ''} onChange={updateFilter('operation')} /></label>
      <label>Result filter<select value={filters.result ?? ''} onChange={updateFilter('result')}><option value="">All results</option><option value="success">Success</option><option value="failed">Failed</option></select></label>
      <label>Correlation ID filter<input value={filters.correlationId ?? ''} onChange={updateFilter('correlation_id')} /></label>
    </div>
    {audit.isPending ? <p role="status">Loading control-plane audit events…</p> : null}
    {audit.isError ? <p role="alert">Unable to load control-plane audit events.</p> : null}
    {audit.data?.events.length === 0 ? <p>No control-plane audit events match these filters.</p> : null}
    {audit.data?.events.length ? <div className="table-region" tabIndex={0} aria-label="Control-plane audit table, horizontally scrollable"><table>
      <thead><tr><th>Time</th><th>Operation</th><th>Agent</th><th>Target</th><th>Result</th><th>Dispatch</th><th>Upstream</th><th>Correlation ID</th></tr></thead>
      <tbody>{audit.data.events.map((event) => <tr key={event.id}>
        <td>{event.timestamp}</td><td>{event.operation}</td>
        <td>{event.agent_id ? <Link to={`/agents/${encodeURIComponent(event.agent_id)}/audit`}>{event.agent_id}</Link> : '—'}</td>
        <td>{event.target ?? '—'}</td><td>{event.result}</td><td>{event.dispatch_state ?? '—'}</td>
        <td>{event.upstream_status ?? '—'}</td><td>{event.correlation_id}</td>
      </tr>)}</tbody>
    </table></div> : null}
  </section>;
}
