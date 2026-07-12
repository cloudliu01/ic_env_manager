import { lazy, Suspense, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { StandaloneAgentProvider } from '../agents/StandaloneAgentProvider';
import { apiClient } from '../shared/api/client';
import { LoginPage } from '../pages/LoginPage';
import { AppShell } from './shell/AppShell';

const TerminalPage = lazy(() => import('../pages/TerminalPage').then((module) => ({ default: module.TerminalPage })));
const ServicesPage = lazy(() => import('../pages/ServiceListPage').then((module) => ({ default: module.ServiceListPage })));
const MetricsPage = lazy(() => import('../pages/MetricsPage').then((module) => ({ default: module.MetricsPage })));
const AuditPage = lazy(() => import('../pages/AuditStatusPage').then((module) => ({ default: module.AuditStatusPage })));
const ObservationsPage = lazy(() => import('../features/observations/ObservationsPage').then((module) => ({ default: module.ObservationsPage })));
const LogsPage = lazy(() => import('../features/logs/LogsPage').then((module) => ({ default: module.LogsPage })));
const ManagerAccessPage = lazy(() => import('../features/agent-settings/ManagerAccessPage').then((module) => ({ default: module.ManagerAccessPage })));

export type AgentIdentity = {
  instance_id: string;
  name: string;
  api_version: string;
  agent_version: string;
  capabilities: string[];
};

function getAgentIdentity(signal?: AbortSignal): Promise<AgentIdentity> {
  return apiClient.request<AgentIdentity>('/api/v2/capabilities', { signal });
}

function CapabilityRoute({ identity, capability, children }: { identity: AgentIdentity; capability: string; children: React.ReactNode }) {
  if (!identity.capabilities.includes(capability)) {
    return <section className="feature-page"><h1 tabIndex={-1}>Feature unavailable</h1><p role="alert">This Agent does not advertise <code>{capability}</code>.</p><a href="/terminal">Return to Terminal</a></section>;
  }
  return children;
}

export default function AgentEntry() {
  const [actor, setActor] = useState<string | null>(null);
  const location = useLocation();

  useEffect(() => {
    apiClient.setUnauthorizedHandler(() => setActor(null));
    return () => apiClient.setUnauthorizedHandler(undefined);
  }, []);

  const identity = useQuery({
    queryKey: ['agent', 'local', 'identity'],
    queryFn: ({ signal }) => getAgentIdentity(signal),
    enabled: Boolean(actor),
    staleTime: Infinity,
    retry: false,
  });

  if (!actor) {
    if (location.pathname === '/') {
      return <Navigate to="/login" replace />;
    }
    return <LoginPage persistSession={false} onAuthenticated={setActor} />;
  }
  if (location.pathname === '/login') {
    return <Navigate to="/terminal" replace />;
  }
  if (identity.isPending) {
    return <main><p role="status">Loading Agent identity…</p></main>;
  }
  if (identity.isError) {
    return <main><h1>Agent identity unavailable</h1><p role="alert">The authenticated Agent identity could not be loaded.</p><button type="button" onClick={() => void identity.refetch()}>Retry</button></main>;
  }

  const terminalVisible = location.pathname === '/terminal';
  return (
    <StandaloneAgentProvider name={identity.data.name} capabilities={identity.data.capabilities}>
      <AppShell identity={identity.data} terminal={<Suspense fallback={<p role="status">Loading Terminal…</p>}><TerminalPage visible={terminalVisible} /></Suspense>}>
        <Suspense fallback={<p role="status">Loading page…</p>}>
          <Routes>
            <Route path="/" element={<Navigate to="/terminal" replace />} />
            <Route path="/login" element={<Navigate to="/terminal" replace />} />
            <Route path="/terminal" element={null} />
            <Route path="/services" element={<CapabilityRoute identity={identity.data} capability="services.v1"><ServicesPage /></CapabilityRoute>} />
            <Route path="/observations" element={<CapabilityRoute identity={identity.data} capability="observations.v2"><ObservationsPage /></CapabilityRoute>} />
            <Route path="/logs" element={<CapabilityRoute identity={identity.data} capability="logs.v2"><LogsPage /></CapabilityRoute>} />
            <Route path="/metrics" element={<CapabilityRoute identity={identity.data} capability="monitoring.snapshot.v1"><MetricsPage /></CapabilityRoute>} />
            <Route path="/audit" element={<CapabilityRoute identity={identity.data} capability="audit.v1"><AuditPage mode="standalone" /></CapabilityRoute>} />
            <Route path="/settings/manager-access" element={<CapabilityRoute identity={identity.data} capability="runtime.v2"><ManagerAccessPage /></CapabilityRoute>} />
            <Route path="*" element={<section className="feature-page"><h1 tabIndex={-1}>Page not found</h1><a href="/terminal">Return to Terminal</a></section>} />
          </Routes>
        </Suspense>
      </AppShell>
    </StandaloneAgentProvider>
  );
}
