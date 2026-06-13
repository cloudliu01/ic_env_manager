import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { loadSessionToken } from '../auth/session';
import { AuditStatusPage } from './AuditStatusPage';
import { HostOverviewPage } from './HostOverviewPage';
import { LoginPage } from './LoginPage';
import { MetricsPage } from './MetricsPage';
import { ServiceListPage } from './ServiceListPage';
import { TerminalPage } from './TerminalPage';

export function AppRoutes() {
  const [actor, setActor] = useState<string | null>(null);
  const [page, setPage] = useState<'overview' | 'terminal' | 'services' | 'metrics' | 'audit'>('overview');

  useEffect(() => {
    const token = loadSessionToken();
    if (token) {
      apiClient.setToken(token);
      setActor('local-admin');
    }
  }, []);

  if (!actor) {
    return <LoginPage onAuthenticated={setActor} />;
  }

  return (
    <main>
      <h1>IC Design Environment Guard</h1>
      <p>Signed in as {actor}</p>
      <nav aria-label="Primary">
        <button type="button" onClick={() => setPage('overview')}>Overview</button>
        <button type="button" onClick={() => setPage('terminal')}>Terminal</button>
        <button type="button" onClick={() => setPage('services')}>Services</button>
        <button type="button" onClick={() => setPage('metrics')}>Metrics</button>
        <button type="button" onClick={() => setPage('audit')}>Audit</button>
      </nav>
      {page === 'overview' ? <HostOverviewPage /> : null}
      {page === 'terminal' ? <TerminalPage /> : null}
      {page === 'services' ? <ServiceListPage /> : null}
      {page === 'metrics' ? <MetricsPage /> : null}
      {page === 'audit' ? <AuditStatusPage /> : null}
    </main>
  );
}
