import { Link, NavLink, Outlet, useLocation, useOutletContext, useParams } from 'react-router-dom';
import { Agent } from './types';
import { useAgent } from './queries';
import { TerminalPage } from '../terminal/TerminalPage';

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

function statusLabel(value: string) {
  return `${value[0].toUpperCase()}${value.slice(1)}`;
}

export type AgentRouteContext = { agent: Agent; agentId: string };

export function useRouteAgent() {
  return useOutletContext<AgentRouteContext>();
}

export function AgentLayout() {
  const { agentId = '' } = useParams();
  const location = useLocation();
  const agent = useAgent(agentId);
  if (agent.isPending) return <p role="status" aria-live="polite">Loading Agent…</p>;
  if (agent.isError || !agent.data) return <p role="alert">The requested Agent could not be loaded.</p>;
  const terminalVisible = location.pathname === `/agents/${encodeURIComponent(agentId)}/terminal`;
  const context = { agent: agent.data, agentId };
  return <section className="feature-page agent-layout">
    <header className="agent-header"><p><Link to="/fleet">Fleet</Link> / {agent.data.display_name}</p>
      <div className="agent-status-summary"><span>Connection <span className={`status status-${agent.data.connection_status}`}>{statusLabel(agent.data.connection_status)}</span></span><span>Workload <span className={`status status-${agent.data.workload_status}`}>{statusLabel(agent.data.workload_status)}</span></span></div>
      {agent.data.transport_warning ? <p className="transport-alert" role="alert">Trusted-LAN connection is unencrypted. Verify this Agent remains on the trusted LAN.</p> : null}
    </header>
    <nav className="agent-tabs" aria-label="Agent detail navigation">
      {detailTabs.map((tab) => {
        const available = !tab.capability || agent.data.capabilities.includes(tab.capability);
        const to = `/agents/${encodeURIComponent(agentId)}/${tab.path}`;
        const reason = tab.capability ? `Unavailable: requires ${tab.capability}` : undefined;
        return available ? <NavLink key={tab.path} to={to}>{tab.label}</NavLink> : <a key={tab.path} href={to} aria-disabled="true" title={reason} onClick={(event) => event.preventDefault()}>{tab.label}<span className="sr-only"> — {reason}</span></a>;
      })}
    </nav>
    <div hidden={!terminalVisible} className="persistent-agent-terminal">
      {agent.data.capabilities.includes('terminals.v1') ? <><h1 tabIndex={-1} className="sr-only">Terminal</h1>
        <TerminalPage target={{ agentId, name: agent.data.display_name, capabilities: agent.data.capabilities }} visible={terminalVisible} /></> : <section><h1 tabIndex={-1}>Feature unavailable</h1><p role="alert">This Agent does not advertise <code>terminals.v1</code>.</p></section>}
    </div>
    {terminalVisible ? null : <Outlet context={context} />}
  </section>;
}
