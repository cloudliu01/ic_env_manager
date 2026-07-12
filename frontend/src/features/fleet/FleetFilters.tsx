import { ChangeEvent } from 'react';
import { AgentFilters } from '../agent-registry/types';

type FleetFiltersProps = {
  filters: AgentFilters;
  onChange: (filters: AgentFilters) => void;
};

export function FleetFilters({ filters, onChange }: FleetFiltersProps) {
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
  </div>;
}
