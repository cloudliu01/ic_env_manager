import { useRouteAgent } from './AgentLayout';
import { EditAgentForm } from './EditAgentForm';

export function AgentSettingsPage() {
  const { agent } = useRouteAgent();
  return <section><h1 tabIndex={-1}>Settings</h1><div className="detail-panel"><h2>Registered configuration</h2>
    <dl className="settings-list"><dt>Endpoint</dt><dd className="data-cell">{agent.endpoint ?? 'Not configured'}</dd><dt>Transport profile</dt><dd>{agent.transport_profile_id ?? 'Default'}</dd><dt>Enabled</dt><dd>{agent.enabled ? 'Enabled' : 'Disabled'}</dd></dl>
  </div><EditAgentForm agent={agent} /></section>;
}
