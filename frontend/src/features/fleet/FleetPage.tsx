import { useDeferredValue, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Agent, AgentFilters } from '../agent-registry/types';
import { FleetCardList } from './FleetCardList';
import { FleetFilters } from './FleetFilters';
import { useFleetOverview } from './queries';
import { FleetTable } from './FleetTable';

function useCompactLayout() {
  const [compact, setCompact] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 767px)').matches);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia('(max-width: 767px)');
    const update = () => setCompact(media.matches);
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);
  return compact;
}

function matches(agent: Agent, filters: AgentFilters) {
  const query = (filters.query ?? '').toLowerCase();
  return (!query || `${agent.display_name} ${agent.agent_id}`.toLowerCase().includes(query))
    && (!filters.connection_status || agent.connection_status === filters.connection_status)
    && (!filters.workload_status || agent.workload_status === filters.workload_status);
}

export function FleetPage() {
  const fleet = useFleetOverview();
  const compact = useCompactLayout();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: AgentFilters = { query: searchParams.get('query') || undefined, connection_status: searchParams.get('connection_status') || undefined, workload_status: searchParams.get('workload_status') || undefined };
  const deferredQuery = useDeferredValue(filters.query);
  const [showSkeleton, setShowSkeleton] = useState(false);
  useEffect(() => {
    if (!fleet.isPending) { setShowSkeleton(false); return; }
    const timer = window.setTimeout(() => setShowSkeleton(true), 300);
    return () => window.clearTimeout(timer);
  }, [fleet.isPending]);
  const updateFilters = (next: AgentFilters) => {
    const params = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => { if (value) params.set(key, value); });
    setSearchParams(params);
  };
  const displayed = (fleet.data?.agents ?? []).filter((agent) => matches(agent, { ...filters, query: deferredQuery }));
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Fleet</h1><p>Registered Agents and their cached health.</p></header>
    <FleetFilters filters={filters} onChange={updateFilters} />
    {fleet.isPending && showSkeleton ? <div className="fleet-skeleton" role="status" aria-live="polite">Loading fleet…</div> : null}
    {fleet.isError ? <p role="alert">Fleet data could not be loaded.</p> : null}
    {fleet.data && displayed.length === 0 ? <div className="empty-state">No Agents match these filters.</div> : null}
    {fleet.data && displayed.length > 0 ? compact ? <FleetCardList agents={displayed} /> : <FleetTable agents={displayed} /> : null}
    <p><Link to="/agents/new">Add agent</Link> · <Link to="/discovery">Discover agents</Link></p>
  </section>;
}
