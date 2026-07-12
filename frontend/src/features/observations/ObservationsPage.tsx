import { AlertTriangle, CheckCircle2, CircleHelp, OctagonAlert } from 'lucide-react';
import { ChangeEvent, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useObservations } from './queries';
import { Observation, ObservationsTarget } from './types';

function Status({ item }: { item: Observation }) {
  const status = item.status ?? 'unknown';
  const Icon = status === 'ok' ? CheckCircle2 : status === 'warning' ? AlertTriangle : status === 'critical' ? OctagonAlert : CircleHelp;
  return <span className={`status status-${status}`}><Icon size={16} aria-hidden="true" />{status[0].toUpperCase() + status.slice(1)}</span>;
}

export function ObservationsPage({ target }: { target: ObservationsTarget }) {
  return <ObservationsPageTarget key={target.agentId} target={target} />;
}

function ObservationsPageTarget({ target }: { target: ObservationsTarget }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get('status') || undefined;
  const query = useObservations(target.agentId, status, target.capabilities.includes('observations.v2'));
  const [expanded, setExpanded] = useState<string | null>(null);
  const items = query.data?.items ?? [];
  const onStatusChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const next = new URLSearchParams(searchParams);
    if (event.target.value) next.set('status', event.target.value); else next.delete('status');
    setSearchParams(next);
  };

  return (
    <section className="feature-page">
      <header className="page-header"><div><h1 tabIndex={-1}>Observations</h1><p>Latest producer results, expiry, and structured diagnostic context.</p></div></header>
      <label>Observation status filter <select aria-label="Observation status filter" value={status ?? ''} onChange={onStatusChange}><option value="">All statuses</option><option value="critical">Critical status</option><option value="warning">Warning status</option><option value="ok">OK status</option></select></label>
      {query.isPending ? <p role="status">Loading observations…</p> : null}
      {query.isError ? <p role="alert">Unable to load observations. Retry from this page.</p> : null}
      {items.length === 0 ? <p className="empty-state">No observations have been received.</p> : null}
      {items.length ? (
        <div className="table-region" tabIndex={0} aria-label="Observations table, horizontally scrollable">
          <table>
            <thead><tr><th scope="col">Namespace / name</th><th scope="col">Status</th><th scope="col">Value</th><th scope="col">Observed</th><th scope="col">Expires</th><th scope="col">Details</th></tr></thead>
            <tbody>{items.map((item, index) => (
              <tr key={item.identity_key ?? index}>
                <th scope="row"><span className="primary-cell">{item.namespace ? `${item.namespace} / ${item.name}` : item.name}</span><span className="secondary-cell">{item.message}</span></th>
                <td><Status item={item} />{item.stale ? <span className="status status-stale"><AlertTriangle size={16} aria-hidden="true" />Stale</span> : null}</td>
                <td className="data-cell">{String(item.value ?? '—')} {item.unit}</td>
                <td className="data-cell">{new Date(item.observed_at).toLocaleString()}</td>
                <td className="data-cell">{new Date(item.expires_at).toLocaleString()}</td>
                <td><button type="button" className="secondary-button" aria-expanded={expanded === item.identity_key} onClick={() => setExpanded(expanded === item.identity_key ? null : item.identity_key)} aria-label={`${expanded === item.identity_key ? 'Hide' : 'Show'} details for ${item.namespace ? `${item.namespace} / ` : ''}${item.name}`}>{expanded === item.identity_key ? 'Hide' : 'Show'}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : null}
      {expanded ? <section className="detail-panel" aria-label="Observation details"><h2>Details</h2><pre>{JSON.stringify(items.find((item) => item.identity_key === expanded)?.details, null, 2)}</pre></section> : null}
    </section>
  );
}
