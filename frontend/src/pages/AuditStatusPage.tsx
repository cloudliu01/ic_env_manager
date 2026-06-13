import { useEffect, useState } from 'react';
import { AuditEvent, listAuditEvents } from '../api/audit';

export function AuditStatusPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAuditEvents()
      .then((response) => setEvents(response.events))
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <section>
      <h2>Audit Status</h2>
      {error ? <p role="alert">{error}</p> : null}
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Operation</th>
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
              <td>{event.target_type}</td>
              <td>{event.result}</td>
              <td>{event.failure_reason ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
