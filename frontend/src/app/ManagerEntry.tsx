import { lazy, Suspense, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { AppShell } from './shell/AppShell';
import { apiClient } from '../shared/api/client';
import { logout } from '../api/auth';
import { clearSessionToken, loadSessionToken } from '../auth/session';
import { LoginPage } from '../pages/LoginPage';
import { AgentLayout, useRouteAgent } from '../features/agent-registry/AgentLayout';
import { CapabilityRoute } from '../shared/components/CapabilityRoute';
import { RouteFocus } from '../shared/components/RouteFocus';

const AgentOverviewPage = lazy(() => import('../features/agent-registry/AgentOverviewPage').then((module) => ({ default: module.AgentOverviewPage })));
const AgentSettingsPage = lazy(() => import('../features/agent-registry/AgentSettingsPage').then((module) => ({ default: module.AgentSettingsPage })));
const AddAgentPage = lazy(() => import('../features/agent-registry/AddAgentPage').then((module) => ({ default: module.AddAgentPage })));
const FleetPage = lazy(() => import('../features/fleet/FleetPage').then((module) => ({ default: module.FleetPage })));
const MonitoringPage = lazy(() => import('../features/fleet/MonitoringPage').then((module) => ({ default: module.MonitoringPage })));
const DiscoveryPage = lazy(() => import('../features/discovery/DiscoveryPage').then((module) => ({ default: module.DiscoveryPage })));
const ServicesPage = lazy(() => import('../features/services/ServicesPage').then((module) => ({ default: module.ServicesPage })));
const ObservationsPage = lazy(() => import('../features/observations/ObservationsPage').then((module) => ({ default: module.ObservationsPage })));
const LogsPage = lazy(() => import('../features/logs/LogsPage').then((module) => ({ default: module.LogsPage })));
const MetricsPage = lazy(() => import('../features/metrics/MetricsPage').then((module) => ({ default: module.MetricsPage })));
const AuditPage = lazy(() => import('../features/audit/AuditPage').then((module) => ({ default: module.AuditPage })));
const ManagerAuditPage = lazy(() => import('../features/audit/ManagerAuditPage').then((module) => ({ default: module.ManagerAuditPage })));

function ManagerLayout({ actor, capabilities, onSignOut }: { actor: string; capabilities: string[]; onSignOut: () => void }) {
  return <AppShell manager actor={actor} onSignOut={onSignOut} identity={{ instance_id: 'manager', name: 'Manager', capabilities }} terminal={null}><RouteFocus /><Outlet /></AppShell>;
}

function ManagerCapabilityRoute({ capabilities, capability, children }: { capabilities: string[]; capability: string; children: React.ReactNode }) {
  if (capabilities.includes(capability)) return children;
  return <section className="feature-page"><h1 tabIndex={-1}>Feature unavailable</h1><p role="alert">This Manager does not advertise <code>{capability}</code>.</p><Link to="/fleet">Return to Fleet</Link></section>;
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
  return <Route path="/agents/:agentId" element={<AgentLayout />}><Route index element={<Navigate to="overview" replace />} /><Route path="overview" element={<AgentOverviewPage />} /><Route path="terminal" element={null} /><Route path="services" element={<AgentServicesPage />} /><Route path="observations" element={<AgentObservationsPage />} /><Route path="logs" element={<AgentLogsPage />} /><Route path="metrics" element={<AgentMetricsPage />} /><Route path="audit" element={<AgentAuditPage />} /><Route path="settings" element={<AgentSettingsPage />} /></Route>;
}

export default function ManagerEntry({ capabilities }: { capabilities: string[] }) {
  const [actor, setActor] = useState<string | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useEffect(() => {
    const expire = () => { clearSessionToken(); apiClient.setToken(null); queryClient.clear(); setActor(null); };
    apiClient.setUnauthorizedHandler(expire);
    const token = loadSessionToken();
    if (token) { apiClient.setToken(token); setActor('local-admin'); }
    setSessionReady(true);
    return () => apiClient.setUnauthorizedHandler(undefined);
  }, [queryClient]);

  async function signOut() {
    try { await logout(); } catch { /* Local cleanup must still complete. */ }
    finally { clearSessionToken(); apiClient.setToken(null); queryClient.clear(); setActor(null); navigate('/login', { replace: true }); }
  }

  if (!sessionReady) return <main><p role="status">Restoring Manager session…</p></main>;
  if (!actor) {
    if (location.pathname === '/') return <Navigate to="/login" replace />;
    return <LoginPage onAuthenticated={setActor} />;
  }
  if (location.pathname === '/login') return <Navigate to="/fleet" replace />;

  const gate = (capability: string, content: React.ReactNode) => <ManagerCapabilityRoute capabilities={capabilities} capability={capability}>{content}</ManagerCapabilityRoute>;
  return <Suspense fallback={<main><p role="status">Loading Manager feature…</p></main>}><Routes>
    <Route element={<ManagerLayout actor={actor} capabilities={capabilities} onSignOut={() => void signOut()} />}>
      <Route path="/fleet" element={gate('fleet.v2', <FleetPage />)} />
      <Route path="/agents/new" element={gate('agent-registry.v2', <AddAgentPage />)} />
      <Route path="/discovery" element={gate('discovery.v2', <DiscoveryPage />)} />
      <Route path="/monitoring" element={gate('fleet.v2', <MonitoringPage />)} />
      <Route path="/audit" element={gate('fleet.v2', <ManagerAuditPage />)} />
      <Route element={gate('agent-registry.v2', <Outlet />)}>{AgentRoutes()}</Route>
      <Route path="/" element={<Navigate to="/fleet" replace />} />
      <Route path="*" element={<section className="feature-page"><h1 tabIndex={-1}>Page not found</h1><Link to="/fleet">Return to Fleet</Link></section>} />
    </Route>
  </Routes></Suspense>;
}
