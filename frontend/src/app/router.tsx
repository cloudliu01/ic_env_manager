import { ChangeEvent, Suspense } from 'react';
import { Link, Navigate, NavLink, Outlet, Route, Routes, useParams, useSearchParams } from 'react-router-dom';
import { useRuntime } from './RuntimeProvider';
import { AppShell } from './shell/AppShell';
import { Agent, ObservationFilters } from '../features/agent-registry/types';
import { useAgent, useAgentObservations } from '../features/agent-registry/queries';
import { useFleetOverview } from '../features/fleet/queries';
import { CapabilityRoute } from '../shared/components/CapabilityRoute';
import { RouteFocus } from '../shared/components/RouteFocus';
import AgentEntry from './AgentEntry';

const detailTabs = [
  { path: 'overview', label: 'Overview' },
  { path: 'terminal', label: 'Terminal', capability: 'terminals.v1' },
  { path: 'services', label: 'Services', capability: 'services.v1' },
  { path: 'observations', label: 'Observations', capability: 'observations.v2' },
  { path: 'logs', label: 'Logs', capability: 'logs.v2' },
  { path: 'metrics', label: 'Metrics', capability: 'monitoring.snapshot.v1' },
  { path: 'audit', label: 'Audit', capability: 'audit.v1' },
  { path: 'settings', label: 'Settings' },
];

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

function FleetPage() {
  const fleet = useFleetOverview();
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Fleet</h1><p>Registered Agents and their cached health.</p></header>
    {fleet.isPending ? <p role="status" aria-live="polite">Loading fleet…</p> : null}
    {fleet.isError ? <QueryError error={fleet.error} /> : null}
    {fleet.data ? <p aria-live="polite">{fleet.data.agents?.length ?? 0} Agents available.</p> : null}
    <p><Link to="/agents/new">Add agent</Link> · <Link to="/discovery">Discover agents</Link></p>
  </section>;
}

function PlaceholderPage({ title }: { title: string }) {
  return <section className="feature-page"><h1 tabIndex={-1}>{title}</h1><p>This route is ready for its dedicated feature.</p></section>;
}

function AgentLayout() {
  const { agentId = '' } = useParams();
  const agent = useAgent(agentId);
  if (agent.isPending) return <p role="status" aria-live="polite">Loading Agent…</p>;
  if (agent.isError) return <QueryError error={agent.error} />;
  return <section className="feature-page">
    <p><Link to="/fleet">Fleet</Link> / {agent.data.display_name}</p>
    <nav aria-label="Agent detail navigation">
      {detailTabs.map((tab) => {
        const available = !tab.capability || agent.data.capabilities.includes(tab.capability);
        const to = `/agents/${encodeURIComponent(agentId)}/${tab.path}`;
        const reason = tab.capability ? `Unavailable: requires ${tab.capability}` : undefined;
        return available ? <NavLink key={tab.path} to={to}>{tab.label}</NavLink> :
          <a key={tab.path} href={to} aria-disabled="true" title={reason} onClick={(event) => event.preventDefault()}>{tab.label}<span className="sr-only"> — {reason}</span></a>;
      })}
    </nav>
    <Outlet context={agent.data} />
  </section>;
}

function useRouteAgent(): { agent: Agent; agentId: string } {
  const { agentId = '' } = useParams();
  const agent = useAgent(agentId);
  if (agent.isPending || agent.isError || !agent.data) throw new Error('Agent route must be rendered inside AgentLayout');
  return { agent: agent.data, agentId };
}

function AgentOverviewPage() {
  const { agent } = useRouteAgent();
  return <section><h1 tabIndex={-1}>Overview</h1><p>{agent.display_name}</p></section>;
}

function AgentCapabilityPlaceholder({ title, capability }: { title: string; capability: string }) {
  const { agent, agentId } = useRouteAgent();
  return <CapabilityRoute agentId={agentId} capability={capability} capabilities={agent.capabilities}><section><h1 tabIndex={-1}>{title}</h1><p>This route is ready for its dedicated feature.</p></section></CapabilityRoute>;
}

function AgentObservationsPage() {
  const { agent, agentId } = useRouteAgent();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: ObservationFilters = { status: searchParams.get('status') || undefined };
  const observations = useAgentObservations(agentId, filters);
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
    <Route path="settings" element={<PlaceholderPage title="Settings" />} />
  </Route>;
}

function ManagerEntry() {
  return <Routes>
    <Route element={<ManagerLayout />}>
      <Route path="/fleet" element={<FleetPage />} />
      <Route path="/agents/new" element={<PlaceholderPage title="Add agent" />} />
      <Route path="/discovery" element={<PlaceholderPage title="Discovery" />} />
      <Route path="/monitoring" element={<PlaceholderPage title="Monitoring" />} />
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
