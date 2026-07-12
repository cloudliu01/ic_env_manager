import { KeyboardEvent, useMemo, useState } from 'react';
import { CircleAlert, CircleCheck, CircleHelp } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { Agent } from '../agent-registry/types';

type SortDirection = 'ascending' | 'descending';

function count(agent: Agent, section: string, key: string) {
  const value = agent.summary?.[section];
  return typeof value === 'object' && value && typeof (value as Record<string, unknown>)[key] === 'number'
    ? (value as Record<string, number>)[key] : 0;
}

function Status({ value }: { value: string }) {
  const Icon = value === 'ready' || value === 'healthy' ? CircleCheck : value === 'unknown' ? CircleHelp : CircleAlert;
  const label = `${value[0].toUpperCase()}${value.slice(1)}`;
  return <span className={`status status-${value}`} aria-label={`${label} status`}><Icon size={14} aria-hidden="true" />{label}</span>;
}

export function FleetTable({ agents }: { agents: Agent[] }) {
  const navigate = useNavigate();
  const [direction, setDirection] = useState<SortDirection>('ascending');
  const [menuAgent, setMenuAgent] = useState<string | null>(null);
  const sorted = useMemo(() => agents.map((agent, index) => ({ agent, index })).sort((a, b) => {
    const result = a.agent.display_name.localeCompare(b.agent.display_name);
    return result === 0 ? a.index - b.index : direction === 'ascending' ? result : -result;
  }).map(({ agent }) => agent), [agents, direction]);
  const openRow = (agent: Agent) => navigate(`/agents/${encodeURIComponent(agent.agent_id)}/overview`);
  const onRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, agent: Agent) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openRow(agent); }
  };

  return <div className="table-region"><table aria-label="Fleet agents">
    <thead><tr>
      <th aria-sort={direction}><button type="button" className="table-sort" onClick={() => setDirection(direction === 'ascending' ? 'descending' : 'ascending')}>Agent</button></th>
      <th>Health</th><th>Transport</th><th>Version</th><th>Observations</th><th>Services</th><th>Last probe</th><th>Actions</th>
    </tr></thead>
    <tbody>{sorted.map((agent) => {
      const critical = count(agent, 'observations', 'critical');
      const unhealthy = count(agent, 'services', 'unhealthy');
      return <tr key={agent.agent_id} tabIndex={0} onClick={() => openRow(agent)} onKeyDown={(event) => onRowKeyDown(event, agent)}>
        <td><span className="primary-cell">{agent.display_name}</span><span className="secondary-cell">{agent.agent_id}</span>{agent.last_error_code ? <span className="secondary-cell">Last error: {agent.last_error_code}</span> : null}</td>
        <td><Status value={agent.connection_status} /><span className="secondary-cell"><Status value={agent.workload_status} /></span></td>
        <td>{agent.endpoint ? <span className="data-cell">{agent.endpoint}</span> : 'No endpoint'}{agent.transport_warning ? <span className="status status-warning">Unencrypted</span> : null}</td>
        <td className="data-cell">{agent.agent_version ?? '—'}</td>
        <td>{critical ? `${critical} critical` : `${count(agent, 'observations', 'total')} total`}</td>
        <td>{unhealthy ? `${unhealthy} unhealthy` : `${count(agent, 'services', 'total')} total`}</td>
        <td className="data-cell">{agent.observed_at ? new Date(agent.observed_at).toLocaleString() : 'Never'}</td>
        <td onClick={(event) => event.stopPropagation()}><Link className="secondary-button" to={`/agents/${encodeURIComponent(agent.agent_id)}/overview`}>Open {agent.display_name}</Link>
          <button type="button" className="icon-button" aria-label={`Actions for ${agent.display_name}`} aria-expanded={menuAgent === agent.agent_id} onClick={() => setMenuAgent(menuAgent === agent.agent_id ? null : agent.agent_id)}>Actions</button>
          {menuAgent === agent.agent_id ? <div className="row-menu" role="menu"><button type="button" role="menuitem" onClick={() => setMenuAgent(null)}>Probe {agent.display_name}</button></div> : null}
        </td>
      </tr>;
    })}</tbody>
  </table></div>;
}
