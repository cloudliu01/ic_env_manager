import { useDeferredValue, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { Agent, AgentFilters } from '../agent-registry/types';
import { probeAgent, updateAgent } from '../agent-registry/enrollment-api';
import { RemoveAgentDialog } from '../agent-registry/RemoveAgentDialog';
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
  const hasProblems = agent.connection_status !== 'ready' || agent.workload_status !== 'healthy';
  return (!query || `${agent.display_name} ${agent.agent_id}`.toLowerCase().includes(query))
    && (!filters.connection_status || agent.connection_status === filters.connection_status)
    && (!filters.workload_status || agent.workload_status === filters.workload_status)
    && (!filters.capability || agent.capabilities.includes(filters.capability))
    && (!filters.problem || (filters.problem === 'has-problems' ? hasProblems : !hasProblems));
}

export function FleetPage() {
  const fleet = useFleetOverview();
  const queryClient = useQueryClient();
  const compact = useCompactLayout();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: AgentFilters = { query: searchParams.get('query') || undefined, connection_status: searchParams.get('connection_status') || undefined, workload_status: searchParams.get('workload_status') || undefined, capability: searchParams.get('capability') || undefined, problem: searchParams.get('problem') || undefined };
  const direction = searchParams.get('order') === 'desc' ? 'descending' : 'ascending';
  const [removeTarget, setRemoveTarget] = useState<Agent | null>(null);
  const [operationMessage, setOperationMessage] = useState('');
  const [operationError, setOperationError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const deferredQuery = useDeferredValue(filters.query);
  const [showSkeleton, setShowSkeleton] = useState(false);
  useEffect(() => {
    if (!fleet.isPending) { setShowSkeleton(false); return; }
    const timer = window.setTimeout(() => setShowSkeleton(true), 300);
    return () => window.clearTimeout(timer);
  }, [fleet.isPending]);
  const updateFilters = (next: AgentFilters) => {
    const params = new URLSearchParams(searchParams);
    for (const key of ['query', 'connection_status', 'workload_status', 'capability', 'problem']) params.delete(key);
    Object.entries(next).forEach(([key, value]) => { if (value) params.set(key, value); });
    setSearchParams(params);
  };
  const refreshFleet = () => queryClient.invalidateQueries({ queryKey: ['fleet'] });
  const runAction = async (action: () => Promise<unknown>, success: string) => {
    setOperationError(''); setOperationMessage('');
    try { await action(); setOperationMessage(success); await refreshFleet(); }
    catch { setOperationError('The Fleet operation could not complete. Retry or inspect the Agent audit trail.'); }
  };
  const refreshAll = async () => {
    setRefreshing(true); setOperationError(''); setOperationMessage('');
    const enabled = (fleet.data?.agents ?? []).filter((agent) => agent.enabled);
    const results = await Promise.allSettled(enabled.map((agent) => probeAgent(agent.agent_id)));
    const failed = results.filter((result) => result.status === 'rejected').length;
    setOperationMessage(`${results.length - failed} refreshed; ${failed} failed.`);
    await refreshFleet();
    setRefreshing(false);
  };
  const setDirection = (next: 'ascending' | 'descending') => {
    const params = new URLSearchParams(searchParams); params.set('sort', 'agent'); params.set('order', next === 'ascending' ? 'asc' : 'desc'); setSearchParams(params);
  };
  const displayed = (fleet.data?.agents ?? []).filter((agent) => matches(agent, { ...filters, query: deferredQuery }));
  const capabilities = [...new Set((fleet.data?.agents ?? []).flatMap((agent) => agent.capabilities))].sort();
  const counts = (fleet.data?.agents ?? []).reduce<Record<string, number>>((result, agent) => { result[agent.connection_status] = (result[agent.connection_status] ?? 0) + 1; return result; }, {});
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Fleet</h1><p>Registered Agents and their cached health.</p></header>
    <div className="page-heading"><div className="fleet-status-summary" aria-label="Fleet status summary">{['ready', 'degraded', 'unavailable', 'disabled'].map((status) => <span key={status}>{status[0].toUpperCase() + status.slice(1)} {counts[status] ?? 0}</span>)}</div><button type="button" className="secondary-button" onClick={() => void refreshAll()} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh all'}</button></div>
    <FleetFilters filters={filters} capabilities={capabilities} onChange={updateFilters} />
    {operationMessage ? <p role="status" aria-label="Fleet refresh result">{operationMessage}</p> : null}
    {operationError ? <p role="alert">{operationError}</p> : null}
    {fleet.isPending && showSkeleton ? <div className="fleet-skeleton" role="status" aria-live="polite">Loading fleet…</div> : null}
    {fleet.isError ? <p role="alert">Fleet data could not be loaded.</p> : null}
    {fleet.data ? <p role="status" aria-live="polite" className="sr-only">{displayed.length} Agent{displayed.length === 1 ? '' : 's'} displayed.</p> : null}
    {fleet.data && displayed.length === 0 ? <div className="empty-state">No Agents match these filters.</div> : null}
    {fleet.data && displayed.length > 0 ? compact ? <FleetCardList agents={displayed} onProbe={(agent) => void runAction(() => probeAgent(agent.agent_id), `${agent.display_name} refreshed.`)} onToggle={(agent) => void runAction(() => updateAgent(agent.agent_id, { enabled: !agent.enabled }), `${agent.display_name} ${agent.enabled ? 'disabled' : 'enabled'}.`)} onRemove={setRemoveTarget} /> : <FleetTable agents={displayed} direction={direction} onDirectionChange={setDirection} onProbe={(agent) => void runAction(() => probeAgent(agent.agent_id), `${agent.display_name} refreshed.`)} onToggle={(agent) => void runAction(() => updateAgent(agent.agent_id, { enabled: !agent.enabled }), `${agent.display_name} ${agent.enabled ? 'disabled' : 'enabled'}.`)} onRemove={setRemoveTarget} /> : null}
    <p><Link to="/agents/new">Add agent</Link> · <Link to="/discovery">Discover agents</Link></p>
    {removeTarget ? <RemoveAgentDialog agentId={removeTarget.agent_id} onClose={() => setRemoveTarget(null)} onRemoved={() => { setRemoveTarget(null); setOperationMessage(`${removeTarget.display_name} removed.`); void refreshFleet(); }} /> : null}
  </section>;
}
