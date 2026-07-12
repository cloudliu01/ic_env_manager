import { Suspense } from 'react';
import { Link, Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { useRuntime } from './RuntimeProvider';
import { AppShell } from './shell/AppShell';
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
import { ServicesPage } from '../features/services/ServicesPage';
import { ObservationsPage } from '../features/observations/ObservationsPage';
import { LogsPage } from '../features/logs/LogsPage';
import { MetricsPage } from '../features/metrics/MetricsPage';
import { AuditPage } from '../features/audit/AuditPage';

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

function AgentFeature({ capability, children }: { capability: string; children: React.ReactNode }) {
  const { agent, agentId } = useRouteAgent();
  return <CapabilityRoute agentId={agentId} capability={capability} capabilities={agent.capabilities}>{children}</CapabilityRoute>;
}

function AgentServicesPage() { const { agent, agentId } = useRouteAgent(); return <AgentFeature capability="services.v1"><ServicesPage target={{ agentId, name: agent.display_name, capabilities: agent.capabilities }} /></AgentFeature>; }
function AgentObservationsPage() { const { agent, agentId } = useRouteAgent(); return <AgentFeature capability="observations.v2"><ObservationsPage target={{ agentId, name: agent.display_name, capabilities: agent.capabilities }} /></AgentFeature>; }
function AgentLogsPage() { const { agent, agentId } = useRouteAgent(); return <AgentFeature capability="logs.v2"><LogsPage target={{ agentId, name: agent.display_name, capabilities: agent.capabilities }} /></AgentFeature>; }
function AgentMetricsPage() { const { agent, agentId } = useRouteAgent(); return <AgentFeature capability="monitoring.snapshot.v1"><MetricsPage target={{ agentId, name: agent.display_name, capabilities: agent.capabilities }} /></AgentFeature>; }
function AgentAuditPage() { const { agent, agentId } = useRouteAgent(); return <AgentFeature capability="audit.v1"><AuditPage target={{ agentId, name: agent.display_name, capabilities: agent.capabilities }} /></AgentFeature>; }

function AgentRoutes() {
  return <Route path="/agents/:agentId" element={<AgentLayout />}>
    <Route index element={<Navigate to="overview" replace />} />
    <Route path="overview" element={<AgentOverviewPage />} />
    <Route path="terminal" element={null} />
    <Route path="services" element={<AgentServicesPage />} />
    <Route path="observations" element={<AgentObservationsPage />} />
    <Route path="logs" element={<AgentLogsPage />} />
    <Route path="metrics" element={<AgentMetricsPage />} />
    <Route path="audit" element={<AgentAuditPage />} />
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
