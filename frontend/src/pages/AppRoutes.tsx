import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { AgentProvider, agentSupports, useActiveAgent } from '../agents/AgentContext';
import { AgentSelector } from '../agents/AgentSelector';
import { clearSessionToken, loadSessionToken } from '../auth/session';
import { AuditStatusPage } from './AuditStatusPage';
import { HostOverviewPage } from './HostOverviewPage';
import { LoginPage } from './LoginPage';
import { MetricsPage } from './MetricsPage';
import { ServiceListPage } from './ServiceListPage';
import { TerminalPage } from './TerminalPage';

export function AppRoutes() {
  const [actor, setActor] = useState<string | null>(null);

  function handleAuthenticationExpired() {
    clearSessionToken();
    apiClient.setToken(null);
    setActor(null);
  }

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
    <AgentProvider onAuthenticationExpired={handleAuthenticationExpired}>
      <AuthenticatedRoutes actor={actor} />
    </AgentProvider>
  );
}

function AuthenticatedRoutes({ actor }: { actor: string }) {
  const [page, setPage] = useState<'overview' | 'terminal' | 'services' | 'metrics' | 'audit'>('overview');
  const [terminalVisited, setTerminalVisited] = useState(false);
  const { activeAgent } = useActiveAgent();
  const supportsTerminals = agentSupports(activeAgent, 'terminals.v1');
  const supportsServices = agentSupports(activeAgent, 'services.v1');
  const supportsMonitoring = agentSupports(activeAgent, 'monitoring.snapshot.v1');
  const supportsAudit = agentSupports(activeAgent, 'audit.v1');

  useEffect(() => {
    if (page === 'terminal') {
      setTerminalVisited(true);
    }
  }, [page]);

  return (
    <main>
        <h1>IC Design Environment Guard</h1>
        <p>Signed in as {actor}</p>
        <AgentSelector />
        <nav aria-label="Primary">
          <button type="button" onClick={() => setPage('overview')}>Overview</button>
          <button type="button" onClick={() => setPage('terminal')} disabled={!supportsTerminals}>Terminal</button>
          <button type="button" onClick={() => setPage('services')} disabled={!supportsServices}>Services</button>
          <button type="button" onClick={() => setPage('metrics')} disabled={!supportsMonitoring}>Metrics</button>
          <button type="button" onClick={() => setPage('audit')} disabled={!supportsAudit}>Audit</button>
        </nav>
        {page === 'overview' ? <HostOverviewPage /> : null}
        {terminalVisited ? (
          <div hidden={page !== 'terminal'}>
            <TerminalPage visible={page === 'terminal'} />
          </div>
        ) : null}
        {page === 'services' ? <ServiceListPage /> : null}
        {page === 'metrics' ? <MetricsPage /> : null}
        {page === 'audit' ? <AuditStatusPage /> : null}
    </main>
  );
}
