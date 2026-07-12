import { useEffect, useState } from 'react';
import { AuditEvent, listAgentAuditEvents } from '../api/audit';
import { supportsCapability, useStandaloneAgent } from '../agents/StandaloneAgentContext';

const AUDIT_LIMIT = 100;

function AuditTable({ events, label }: { events: AuditEvent[]; label: string }) {
  return (
    <div className="table-region" tabIndex={0} aria-label={`${label}, horizontally scrollable`}><table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Operation</th>
          <th>Agent</th>
          <th>Target</th>
          <th>Result</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <tr key={event.id}>
            <td>{event.timestamp}</td>
            <td>{event.operation}</td>
            <td>{event.agent_id ?? ''}</td>
            <td>{event.target_type}</td>
            <td>{event.result}</td>
            <td>{event.failure_reason ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

export function AuditStatusPage() {
  const { agentId, name, capabilities } = useStandaloneAgent();
  const supportsAudit = supportsCapability(capabilities, 'audit.v1');
  const [agentEvents, setAgentEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supportsAudit) {
      setAgentEvents([]);
      return;
    }

    const controller = new AbortController();
    let active = true;
    listAgentAuditEvents(agentId, AUDIT_LIMIT, controller.signal)
      .then((response) => { if (active) setAgentEvents(response.events); })
      .catch((err: Error) => { if (active && err.name !== 'AbortError') setError(err.message); });
    return () => { active = false; controller.abort(); };
  }, [agentId, supportsAudit]);

  return (
    <section>
      <h1 tabIndex={-1}>Audit Status</h1>
      {error ? <p role="alert">{error}</p> : null}

      <article>
        <h2>Agent audit</h2>
        <p>{name}</p>
        {!supportsAudit ? <p>This Agent does not support audit.</p> : null}
        {supportsAudit ? <AuditTable events={agentEvents} label="Agent audit table" /> : null}
      </article>
    </section>
  );
}
