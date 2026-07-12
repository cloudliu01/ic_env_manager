import { ChangeEvent, useState } from 'react';
import { useAuditEvents } from './queries';
import { AuditEvent, AuditTarget } from './types';

function AuditTable({ events }: { events: AuditEvent[] }) {
  return <div className="table-region" tabIndex={0} aria-label="Agent audit table, horizontally scrollable"><table><thead><tr><th>Time</th><th>Operation</th><th>Agent</th><th>Target</th><th>Result</th><th>Reason</th></tr></thead><tbody>{events.map((event) => <tr key={event.id}><td>{event.timestamp}</td><td>{event.operation}</td><td>{event.agent_id ?? ''}</td><td>{event.target_type}</td><td>{event.result}</td><td>{event.failure_reason ?? ''}</td></tr>)}</tbody></table></div>;
}

export function AuditPage({ target }: { target: AuditTarget }) {
  const [operation, setOperation] = useState('');
  const [result, setResult] = useState('');
  const [cursor, setCursor] = useState<string | undefined>();
  const supported = target.capabilities.includes('audit.v1');
  const audit = useAuditEvents(target.agentId, { operation: operation || undefined, result: result || undefined, cursor }, supported);
  const onFilter = (setter: (value: string) => void) => (event: ChangeEvent<HTMLInputElement>) => { setCursor(undefined); setter(event.target.value); };
  return <section className="feature-page"><h1 tabIndex={-1}>Audit Status</h1>
    <label>Operation filter <input value={operation} onChange={onFilter(setOperation)} /></label>
    <label>Result filter <input value={result} onChange={onFilter(setResult)} /></label>
    {!supported ? <p role="status">This Agent does not support audit.</p> : null}
    {audit.isPending ? <p role="status">Loading audit records…</p> : null}
    {audit.isError ? <p role="alert">Unable to load audit records.</p> : null}
    {supported && audit.data ? <><AuditTable events={audit.data.events} />{audit.data.next_cursor ? <button type="button" onClick={() => setCursor(audit.data?.next_cursor ?? undefined)}>Next page</button> : null}</> : null}
  </section>;
}
