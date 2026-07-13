import { ChangeEvent } from 'react';
import { AgentFilters } from '../agent-registry/types';

type FleetFiltersProps = {
  filters: AgentFilters;
  capabilities: string[];
  onChange: (filters: AgentFilters) => void;
};

export function FleetFilters({ filters, capabilities, onChange }: FleetFiltersProps) {
  const update = (key: keyof AgentFilters) => (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    onChange({ ...filters, [key]: event.target.value || undefined });
  };

  return <div className="fleet-filters" aria-label="Fleet filters">
    <label>Search Agents<input type="search" value={filters.query ?? ''} onChange={update('query')} /></label>
    <label>Connection status<select aria-label="Connection status" value={filters.connection_status ?? ''} onChange={update('connection_status')}>
      <option value="">All connections</option><option value="ready">Ready</option><option value="degraded">Degraded</option><option value="unavailable">Unavailable</option><option value="unknown">Unknown</option><option value="disabled">Disabled</option>
    </select></label>
    <label>Workload status<select aria-label="Workload status" value={filters.workload_status ?? ''} onChange={update('workload_status')}>
      <option value="">All workloads</option><option value="healthy">Healthy</option><option value="warning">Warning</option><option value="critical">Critical</option><option value="stale">Stale</option><option value="unknown">Unknown</option>
    </select></label>
    <label>Capability<select aria-label="Capability" value={filters.capability ?? ''} onChange={update('capability')}>
      <option value="">All capabilities</option>{capabilities.map((capability) => <option key={capability} value={capability}>{capability}</option>)}
    </select></label>
    <label>Problems<select aria-label="Problems" value={filters.problem ?? ''} onChange={update('problem')}>
      <option value="">All Agents</option><option value="has-problems">Has problems</option><option value="no-problems">No problems</option>
    </select></label>
  </div>;
}
