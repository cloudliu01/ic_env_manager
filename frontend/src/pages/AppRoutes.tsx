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
  const [view, setView] = useState<'fleet' | 'host'>('fleet');
  const [hostPage, setHostPage] = useState<'terminal' | 'services' | 'metrics' | 'audit'>('services');
  const [terminalVisited, setTerminalVisited] = useState(false);
  const { activeAgent } = useActiveAgent();
  const supportsTerminals = agentSupports(activeAgent, 'terminals.v1');
  const supportsServices = agentSupports(activeAgent, 'services.v1');
  const supportsMonitoring = agentSupports(activeAgent, 'monitoring.snapshot.v1');
  const supportsAudit = agentSupports(activeAgent, 'audit.v1');

  useEffect(() => {
    if (view === 'host' && hostPage === 'terminal') {
      setTerminalVisited(true);
    }
  }, [hostPage, view]);

  function openHostWorkspace() {
    setView('host');
    setHostPage(supportsServices ? 'services' : supportsTerminals ? 'terminal' : supportsMonitoring ? 'metrics' : 'audit');
  }

  return (
    <main>
        <h1>IC Design Environment Guard</h1>
        <p>Signed in as {actor}</p>
        <nav aria-label="Primary">
          <button type="button" onClick={() => setView('fleet')}>Fleet Overview</button>
          <button type="button" onClick={openHostWorkspace} disabled={!activeAgent}>Host: {activeAgent?.name ?? 'none'}</button>
        </nav>
        {view === 'fleet' ? <HostOverviewPage onOpenHost={openHostWorkspace} /> : null}
        {view === 'host' ? (
          <section className="host-workspace">
            <div className="host-workspace-header">
              <div>
                <p className="eyebrow">Host Workspace</p>
                <h2>{activeAgent?.name ?? 'No active host'}</h2>
                <p>{activeAgent?.id ?? 'Select a ready host from Fleet Overview.'}</p>
              </div>
              <AgentSelector />
            </div>
            <nav aria-label="Host workspace">
              <button type="button" onClick={() => setHostPage('terminal')} disabled={!supportsTerminals}>Terminal</button>
              <button type="button" onClick={() => setHostPage('services')} disabled={!supportsServices}>Services</button>
              <button type="button" onClick={() => setHostPage('metrics')} disabled={!supportsMonitoring}>Metrics</button>
              <button type="button" onClick={() => setHostPage('audit')} disabled={!supportsAudit}>Audit</button>
            </nav>
          </section>
        ) : null}
        {terminalVisited ? (
          <div hidden={view !== 'host' || hostPage !== 'terminal'}>
            <TerminalPage visible={view === 'host' && hostPage === 'terminal'} />
          </div>
        ) : null}
        {view === 'host' && hostPage === 'services' ? <ServiceListPage /> : null}
        {view === 'host' && hostPage === 'metrics' ? <MetricsPage /> : null}
        {view === 'host' && hostPage === 'audit' ? <AuditStatusPage /> : null}
    </main>
  );
}
