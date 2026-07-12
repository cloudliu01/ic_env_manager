import { ChangeEvent, Suspense } from 'react';
import { Link, Navigate, Outlet, Route, Routes, useSearchParams } from 'react-router-dom';
import { useRuntime } from './RuntimeProvider';
import { AppShell } from './shell/AppShell';
import { ObservationFilters } from '../features/agent-registry/types';
import { useAgentObservations } from '../features/agent-registry/queries';
import { AgentLayout, useRouteAgent } from '../features/agent-registry/AgentLayout';
import { AgentOverviewPage } from '../features/agent-registry/AgentOverviewPage';
import { AgentSettingsPage } from '../features/agent-registry/AgentSettingsPage';
import { AddAgentPage } from '../features/agent-registry/AddAgentPage';
import { FleetPage } from '../features/fleet/FleetPage';
import { MonitoringPage } from '../features/fleet/MonitoringPage';
import { DiscoveryPage } from '../features/discovery/DiscoveryPage';
import { CapabilityRoute } from '../shared/components/CapabilityRoute';
import { RouteFocus } from '../shared/components/RouteFocus';
import AgentEntry from './AgentEntry';

function QueryError({ error }: { error: unknown }) {
  const correlationId = typeof error === 'object' && error && 'correlationId' in error
    && typeof error.correlationId === 'string' ? error.correlationId : undefined;
  return (
    <div role="alert">
      <p>{error instanceof Error ? error.message : 'The requested data could not be loaded.'}</p>
      {correlationId ? <button type="button" onClick={() => void navigator.clipboard?.writeText(correlationId)}>Copy correlation ID</button> : null}
    </div>
  );
}

function ManagerLayout() {
  return (
    <AppShell manager identity={{ instance_id: 'manager', name: 'Manager', capabilities: [] }} terminal={null}>
      <RouteFocus />
      <Outlet />
    </AppShell>
  );
}

function PlaceholderPage({ title }: { title: string }) {
  return <section className="feature-page"><h1 tabIndex={-1}>{title}</h1><p>This route is ready for its dedicated feature.</p></section>;
}

function AgentCapabilityPlaceholder({ title, capability }: { title: string; capability: string }) {
  const { agent, agentId } = useRouteAgent();
  return <CapabilityRoute agentId={agentId} capability={capability} capabilities={agent.capabilities}><section><h1 tabIndex={-1}>{title}</h1><p>This route is ready for its dedicated feature.</p></section></CapabilityRoute>;
}

function AgentObservationsPage() {
  const { agent, agentId } = useRouteAgent();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: ObservationFilters = { status: searchParams.get('status') || undefined };
  const observations = useAgentObservations(agentId, filters, agent.capabilities.includes('observations.v2'));
  const onStatusChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const next = new URLSearchParams(searchParams);
    if (event.target.value) next.set('status', event.target.value); else next.delete('status');
    setSearchParams(next);
  };
  return <CapabilityRoute agentId={agentId} capability="observations.v2" capabilities={agent.capabilities}>
    <section><h1 tabIndex={-1}>Observations</h1><label>Observation status filter <select aria-label="Observation status filter" value={filters.status ?? ''} onChange={onStatusChange}><option value="">All statuses</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="ok">OK</option></select></label>
      {observations.isPending ? <p role="status" aria-live="polite">Loading observations…</p> : null}
      {observations.isError ? <QueryError error={observations.error} /> : null}
      {observations.data?.map((item, index) => <p key={item.identity_key ?? `${item.name}-${index}`}>{item.name}</p>)}
    </section>
  </CapabilityRoute>;
}

function AgentRoutes() {
  return <Route path="/agents/:agentId" element={<AgentLayout />}>
    <Route index element={<Navigate to="overview" replace />} />
    <Route path="overview" element={<AgentOverviewPage />} />
    <Route path="terminal" element={<AgentCapabilityPlaceholder title="Terminal" capability="terminals.v1" />} />
    <Route path="services" element={<AgentCapabilityPlaceholder title="Services" capability="services.v1" />} />
    <Route path="observations" element={<AgentObservationsPage />} />
    <Route path="logs" element={<AgentCapabilityPlaceholder title="Logs" capability="logs.v2" />} />
    <Route path="metrics" element={<AgentCapabilityPlaceholder title="Metrics" capability="monitoring.snapshot.v1" />} />
    <Route path="audit" element={<AgentCapabilityPlaceholder title="Audit" capability="audit.v1" />} />
    <Route path="settings" element={<AgentSettingsPage />} />
  </Route>;
}

function ManagerEntry() {
  return <Routes>
    <Route element={<ManagerLayout />}>
      <Route path="/fleet" element={<FleetPage />} />
      <Route path="/agents/new" element={<AddAgentPage />} />
      <Route path="/discovery" element={<DiscoveryPage />} />
      <Route path="/monitoring" element={<MonitoringPage />} />
      <Route path="/audit" element={<PlaceholderPage title="Audit" />} />
      {AgentRoutes()}
      <Route path="/" element={<Navigate to="/fleet" replace />} />
      <Route path="*" element={<section className="feature-page"><h1 tabIndex={-1}>Page not found</h1><Link to="/fleet">Return to Fleet</Link></section>} />
    </Route>
  </Routes>;
}

export function RuntimeRouter() {
  const runtime = useRuntime();
  return <Suspense fallback={<main><p role="status">Loading application…</p></main>}>
    {runtime.mode === 'agent' ? <AgentEntry /> : <ManagerEntry />}
  </Suspense>;
}
