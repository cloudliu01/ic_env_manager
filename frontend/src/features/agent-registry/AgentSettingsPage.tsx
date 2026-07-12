import { useRouteAgent } from './AgentLayout';

export function AgentSettingsPage() {
  const { agent } = useRouteAgent();
  return <section><h1 tabIndex={-1}>Settings</h1><div className="detail-panel"><h2>Registered configuration</h2>
    <dl className="settings-list"><dt>Endpoint</dt><dd className="data-cell">{agent.endpoint ?? 'Not configured'}</dd><dt>Transport profile</dt><dd>{agent.transport_profile_id ?? 'Default'}</dd><dt>Enabled</dt><dd>{agent.enabled ? 'Enabled' : 'Disabled'}</dd></dl>
    <p className="secondary-cell">Configuration changes are available from the Fleet management flow.</p>
  </div></section>;
}
