import { useEffect, useState } from 'react';
import { AuditEvent, listAgentAuditEvents, listGatewayAuditEvents } from '../api/audit';
import { agentSupports, useActiveAgent } from '../agents/AgentStateContext';

const AUDIT_LIMIT = 100;

function AuditTable({ events }: { events: AuditEvent[] }) {
  return (
    <table>
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
    </table>
  );
}

export function AuditStatusPage() {
  const { activeAgent, activeAgentId } = useActiveAgent();
  const supportsAudit = agentSupports(activeAgent, 'audit.v1');
  const [gatewayEvents, setGatewayEvents] = useState<AuditEvent[]>([]);
  const [agentEvents, setAgentEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listGatewayAuditEvents(AUDIT_LIMIT)
      .then((response) => setGatewayEvents(response.events))
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!activeAgentId || !supportsAudit) {
      setAgentEvents([]);
      return;
    }

    listAgentAuditEvents(activeAgentId, AUDIT_LIMIT)
      .then((response) => setAgentEvents(response.events))
      .catch((err: Error) => setError(err.message));
  }, [activeAgentId, supportsAudit]);

  return (
    <section>
      <h2>Audit Status</h2>
      {error ? <p role="alert">{error}</p> : null}

      <article>
        <h3>Gateway audit</h3>
        <AuditTable events={gatewayEvents} />
      </article>

      <article>
        <h3>Agent audit</h3>
        <p>{activeAgent?.name ?? activeAgentId ?? 'No active agent selected.'}</p>
        {activeAgentId && !supportsAudit ? <p>Selected agent does not support audit.</p> : null}
        {activeAgentId && supportsAudit ? <AuditTable events={agentEvents} /> : null}
      </article>
    </section>
  );
}
