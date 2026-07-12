import { AlertTriangle, FileText } from 'lucide-react';
import { useState } from 'react';
import { useLogs, useLogTail } from './queries';
import { LogsTarget } from './types';

export function LogsPage({ target }: { target: LogsTarget }) {
  const logs = useLogs(target.agentId, target.capabilities.includes('logs.v2'));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const tail = useLogTail(target.agentId, selectedId);

  return (
    <section className="feature-page">
      <header className="page-header"><div><h1 tabIndex={-1}>Logs</h1><p>Registered log metadata and on-demand latest output.</p></div></header>
      {logs.isPending ? <p role="status">Loading log sources…</p> : null}
      {logs.isError ? <p role="alert">Unable to load log sources.</p> : null}
      {logs.data?.length === 0 ? <p className="empty-state">No log sources are registered.</p> : null}
      {logs.data?.length ? <div className="table-region" tabIndex={0} aria-label="Log sources table, horizontally scrollable"><table>
        <thead><tr><th scope="col">Log ID</th><th scope="col">Path</th><th scope="col">Last updated</th><th scope="col">Freshness</th><th scope="col">Action</th></tr></thead>
        <tbody>{logs.data.map((log) => <tr key={log.id}>
          <th scope="row"><span className="icon-label"><FileText size={16} aria-hidden="true" />{log.id}</span></th>
          <td className="data-cell">{log.path}</td><td className="data-cell">{new Date(log.last_updated).toLocaleString()}</td>
          <td>{log.stale ? <span className="status status-stale"><AlertTriangle size={16} aria-hidden="true" />Stale</span> : <span className="status status-ok">Current</span>}</td>
          <td><button type="button" className="secondary-button" onClick={() => setSelectedId(log.id)}>Tail {log.id}</button></td>
        </tr>)}</tbody>
      </table></div> : null}
      {selectedId ? <section className="detail-panel" aria-live="polite"><h2>Latest 100 lines — {selectedId}</h2>
        {tail.isPending ? <p role="status">Loading log output…</p> : null}
        {tail.isError ? <p role="alert">Unable to read this log.</p> : null}
        {tail.data?.truncated ? <p className="truncation-notice"><AlertTriangle size={16} aria-hidden="true" />Output truncated to the latest 100 lines.</p> : tail.data ? <p>Complete output returned ({tail.data.line_count} lines).</p> : null}
        {tail.data ? <pre>{tail.data.lines.join('\n')}</pre> : null}
      </section> : null}
    </section>
  );
}
