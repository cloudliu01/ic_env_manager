import { AlertTriangle, CheckCircle2, CircleHelp, OctagonAlert } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { listObservations, Observation } from './api';

function Status({ item }: { item: Observation }) {
  const Icon = item.status === 'ok' ? CheckCircle2 : item.status === 'warning' ? AlertTriangle : item.status === 'critical' ? OctagonAlert : CircleHelp;
  return <span className={`status status-${item.status}`}><Icon size={16} aria-hidden="true" />{item.status[0].toUpperCase() + item.status.slice(1)}</span>;
}

export function ObservationsPage() {
  const query = useQuery({
    queryKey: ['agent', 'local', 'observations'],
    queryFn: ({ signal }) => listObservations(signal),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <section className="feature-page">
      <header className="page-header"><div><h1 tabIndex={-1}>Observations</h1><p>Latest producer results, expiry, and structured diagnostic context.</p></div></header>
      {query.isPending ? <p role="status">Loading observations…</p> : null}
      {query.isError ? <p role="alert">Unable to load observations. Retry from this page.</p> : null}
      {query.data?.items.length === 0 ? <p className="empty-state">No observations have been received.</p> : null}
      {query.data?.items.length ? (
        <div className="table-region" tabIndex={0} aria-label="Observations table, horizontally scrollable">
          <table>
            <thead><tr><th scope="col">Namespace / name</th><th scope="col">Status</th><th scope="col">Value</th><th scope="col">Observed</th><th scope="col">Expires</th><th scope="col">Details</th></tr></thead>
            <tbody>{query.data.items.map((item) => (
              <tr key={item.identity_key}>
                <th scope="row"><span className="primary-cell">{item.namespace} / {item.name}</span><span className="secondary-cell">{item.message}</span></th>
                <td><Status item={item} />{item.stale ? <span className="status status-stale"><AlertTriangle size={16} aria-hidden="true" />Stale</span> : null}</td>
                <td className="data-cell">{String(item.value ?? '—')} {item.unit}</td>
                <td className="data-cell">{new Date(item.observed_at).toLocaleString()}</td>
                <td className="data-cell">{new Date(item.expires_at).toLocaleString()}</td>
                <td><button type="button" className="secondary-button" aria-expanded={expanded === item.identity_key} onClick={() => setExpanded(expanded === item.identity_key ? null : item.identity_key)} aria-label={`${expanded === item.identity_key ? 'Hide' : 'Show'} details for ${item.namespace} / ${item.name}`}>{expanded === item.identity_key ? 'Hide' : 'Show'}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : null}
      {expanded ? <section className="detail-panel" aria-label="Observation details"><h2>Details</h2><pre>{JSON.stringify(query.data?.items.find((item) => item.identity_key === expanded)?.details, null, 2)}</pre></section> : null}
    </section>
  );
}
