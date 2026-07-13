import { KeyboardEvent, useMemo, useState } from 'react';
import { CircleAlert, CircleCheck, CircleHelp } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { Agent } from '../agent-registry/types';

type SortDirection = 'ascending' | 'descending';

type FleetActions = {
  onProbe: (agent: Agent) => void;
  onToggle: (agent: Agent) => void;
  onRemove: (agent: Agent) => void;
};

function count(agent: Agent, section: string, key: string): number | null {
  const value = agent.summary?.[section];
  return typeof value === 'object' && value && typeof (value as Record<string, unknown>)[key] === 'number'
    ? (value as Record<string, number>)[key] : null;
}

const connectionSeverity: Record<string, number> = { unavailable: 0, degraded: 1, unknown: 2, ready: 3, disabled: 4 };
const workloadSeverity: Record<string, number> = { critical: 0, warning: 1, stale: 2, unknown: 3, healthy: 4 };

function Status({ value }: { value: string }) {
  const Icon = value === 'ready' || value === 'healthy' ? CircleCheck : value === 'unknown' ? CircleHelp : CircleAlert;
  const label = `${value[0].toUpperCase()}${value.slice(1)}`;
  return <span className={`status status-${value}`} aria-label={`${label} status`}><Icon size={14} aria-hidden="true" />{label}</span>;
}

export function FleetTable({ agents, direction, sortByName, onDirectionChange, onProbe, onToggle, onRemove }: { agents: Agent[]; direction: SortDirection; sortByName: boolean; onDirectionChange: (direction: SortDirection) => void } & FleetActions) {
  const navigate = useNavigate();
  const [menuAgent, setMenuAgent] = useState<string | null>(null);
  const sorted = useMemo(() => agents.map((agent, index) => ({ agent, index })).sort((a, b) => {
    const name = a.agent.display_name.localeCompare(b.agent.display_name);
    if (sortByName) return name === 0 ? a.index - b.index : direction === 'ascending' ? name : -name;
    const connection = (connectionSeverity[a.agent.connection_status] ?? 5) - (connectionSeverity[b.agent.connection_status] ?? 5);
    const workload = (workloadSeverity[a.agent.workload_status] ?? 5) - (workloadSeverity[b.agent.workload_status] ?? 5);
    return connection || workload || name || a.index - b.index;
  }).map(({ agent }) => agent), [agents, direction, sortByName]);
  const openRow = (agent: Agent) => navigate(`/agents/${encodeURIComponent(agent.agent_id)}/overview`);
  const onRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, agent: Agent) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openRow(agent); }
  };

  return <div className="table-region"><table className="fleet-table" aria-label="Fleet agents">
    <thead><tr>
      <th aria-sort={sortByName ? direction : undefined}><button type="button" className="table-sort" onClick={() => onDirectionChange(!sortByName || direction === 'descending' ? 'ascending' : 'descending')}>Agent</button></th>
      <th aria-sort={!sortByName ? 'ascending' : undefined}>Health</th><th>Transport</th><th>Version</th><th>Observations</th><th>Services</th><th>Last probe</th><th>Actions</th>
    </tr></thead>
    <tbody>{sorted.map((agent) => {
      const observationTotal = count(agent, 'observations', 'total');
      const serviceTotal = count(agent, 'services', 'total');
      return <tr className="fleet-row" key={agent.agent_id} tabIndex={0} onClick={() => openRow(agent)} onKeyDown={(event) => onRowKeyDown(event, agent)}>
        <td className="fleet-agent-cell"><span className="primary-cell">{agent.display_name}</span><span className="secondary-cell">{agent.agent_id}</span>{agent.last_error_code ? <span className="row-last-error" title={`Last error: ${agent.last_error_code}`}>Last error: {agent.last_error_code}</span> : null}</td>
        <td className="fleet-health-cell"><Status value={agent.connection_status} /><Status value={agent.workload_status} /></td>
        <td className="fleet-transport-cell">{agent.endpoint ? <span className="data-cell" title={agent.endpoint}>{agent.endpoint}</span> : 'No endpoint'}{agent.transport_warning ? <span className="status status-warning">Unencrypted</span> : null}</td>
        <td className="data-cell fleet-cell">{agent.agent_version || agent.api_version ? <>Agent {agent.agent_version ?? '—'}<br />API {agent.api_version ?? '—'}</> : '—'}</td>
        <td className="fleet-cell">{observationTotal === null ? 'No summary' : `${observationTotal} total · ${count(agent, 'observations', 'critical') ?? 0} critical · ${count(agent, 'observations', 'warning') ?? 0} warning · ${count(agent, 'observations', 'stale') ?? 0} stale`}</td>
        <td className="fleet-cell">{serviceTotal === null ? 'No summary' : `${count(agent, 'services', 'running') ?? 0} running / ${serviceTotal} total · ${count(agent, 'services', 'unhealthy') ?? 0} unhealthy`}</td>
        <td className="data-cell fleet-cell" title={agent.observed_at ?? undefined}>{agent.observed_at ? new Date(agent.observed_at).toLocaleString() : 'Never'}</td>
        <td className="fleet-actions" onClick={(event) => event.stopPropagation()}><Link className="secondary-button fleet-open" to={`/agents/${encodeURIComponent(agent.agent_id)}/overview`}>Open {agent.display_name}</Link><button type="button" className="secondary-button" onClick={() => onProbe(agent)}>Probe {agent.display_name}</button>
          <button type="button" className="icon-button fleet-actions-trigger" aria-label={`Actions for ${agent.display_name}`} aria-expanded={menuAgent === agent.agent_id} onClick={() => setMenuAgent(menuAgent === agent.agent_id ? null : agent.agent_id)}>Actions</button>
          {menuAgent === agent.agent_id ? <div className="row-menu" role="menu">
            <button type="button" role="menuitem" onClick={() => { setMenuAgent(null); onToggle(agent); }}>{agent.enabled ? 'Disable' : 'Enable'} {agent.display_name}</button>
            <Link role="menuitem" to={`/agents/${encodeURIComponent(agent.agent_id)}/settings`} onClick={() => setMenuAgent(null)}>Edit {agent.display_name}</Link>
            <button type="button" role="menuitem" onClick={() => { setMenuAgent(null); onRemove(agent); }}>Remove {agent.display_name}</button>
          </div> : null}
        </td>
      </tr>;
    })}</tbody>
  </table></div>;
}
