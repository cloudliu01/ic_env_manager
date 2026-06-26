import { useMemo, useState } from 'react';
import { useActiveAgent } from '../agents/AgentContext';
import { FleetHost } from '../api/fleet';

type SortKey = 'status' | 'name' | 'freshness';

const statusRank: Record<string, number> = {
  ready: 0,
  degraded: 1,
  unavailable: 2,
  unknown: 3,
  disabled: 4,
};

export function HostOverviewPage({ onOpenHost }: { onOpenHost?: (hostId: string) => void }) {
  const { fleetHosts, fleetLoading, fleetError, refreshFleet, setActiveAgentId, setHostEnabled } = useActiveAgent();
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sortKey, setSortKey] = useState<SortKey>('status');
  const [operationMessage, setOperationMessage] = useState<string | null>(null);
  const [pendingHostId, setPendingHostId] = useState<string | null>(null);

  const visibleHosts = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return [...fleetHosts]
      .filter((host) => statusFilter === 'all' || host.status === statusFilter)
      .filter((host) => !normalizedQuery || `${host.name} ${host.id}`.toLowerCase().includes(normalizedQuery))
      .sort((left, right) => {
        if (sortKey === 'name') {
          return left.name.localeCompare(right.name);
        }
        if (sortKey === 'freshness') {
          return Date.parse(right.observed_at ?? '') - Date.parse(left.observed_at ?? '');
        }
        return (statusRank[left.status] ?? 99) - (statusRank[right.status] ?? 99) || left.name.localeCompare(right.name);
      });
  }, [fleetHosts, query, sortKey, statusFilter]);

  const counts = useMemo(() => fleetHosts.reduce<Record<string, number>>((acc, host) => {
    acc[host.status] = (acc[host.status] ?? 0) + 1;
    return acc;
  }, {}), [fleetHosts]);

  function openHost(host: FleetHost) {
    if (!host.enabled || host.status !== 'ready') {
      return;
    }
    setActiveAgentId(host.id);
    onOpenHost?.(host.id);
  }

  async function toggleEnabled(host: FleetHost) {
    setPendingHostId(host.id);
    setOperationMessage(null);
    try {
      await setHostEnabled(host.id, !host.enabled);
      setOperationMessage(`${host.name} ${host.enabled ? 'disabled' : 'enabled'}.`);
    } catch (err) {
      setOperationMessage(err instanceof Error ? err.message : `Unable to update ${host.name}.`);
    } finally {
      setPendingHostId(null);
    }
  }

  return (
    <section className="fleet-page">
      <div className="fleet-hero">
        <div>
          <p className="eyebrow">Fleet</p>
          <h2>Fleet Overview</h2>
          <p>{fleetHosts.length} hosts · {Object.entries(counts).map(([status, count]) => `${count} ${status}`).join(' · ') || 'no status yet'}</p>
        </div>
        <button type="button" onClick={() => void refreshFleet()} disabled={fleetLoading}>Refresh all</button>
      </div>

      <div className="fleet-filters">
        <label>
          Search hosts
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="name or id" />
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            <option value="ready">Ready</option>
            <option value="degraded">Degraded</option>
            <option value="unavailable">Unavailable</option>
            <option value="unknown">Unknown</option>
            <option value="disabled">Disabled</option>
          </select>
        </label>
        <label>
          Sort
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}>
            <option value="status">Status</option>
            <option value="name">Name</option>
            <option value="freshness">Freshness</option>
          </select>
        </label>
      </div>

      {fleetError ? <p className="fleet-error">{fleetError}</p> : null}
      {operationMessage ? <p className="fleet-message">{operationMessage}</p> : null}
      {!fleetLoading && fleetHosts.length === 0 ? <p>No hosts are configured.</p> : null}
      {visibleHosts.length === 0 && fleetHosts.length > 0 ? <p>No hosts match the current filters.</p> : null}

      <div className="fleet-grid">
        {visibleHosts.map((host) => {
          const canOpen = host.enabled && host.status === 'ready';
          const freshness = host.observed_at ? `Observed ${new Date(host.observed_at).toLocaleString()}` : 'No observation yet';
          return (
            <article className="fleet-card" key={host.id}>
              <div className="fleet-card-header">
                <div>
                  <h3>{host.name}</h3>
                  <p>{host.id}</p>
                </div>
                <span className={`status-badge status-${host.status}`}>{host.status}</span>
              </div>
              <p>{freshness}</p>
              {host.stale_after ? <p>Stale after {new Date(host.stale_after).toLocaleString()}</p> : null}
              <div className="fleet-capabilities" aria-label={`${host.name} capabilities`}>
                {(host.capabilities ?? []).length > 0 ? host.capabilities?.map((capability) => <span key={capability}>{capability}</span>) : <span>No capabilities</span>}
              </div>
              {host.summary ? (
                <p>CPU {host.summary.cpu_percent ?? 'n/a'}% · Memory {host.summary.mem_percent ?? 'n/a'}% · Load {host.summary.load1 ?? 'n/a'}</p>
              ) : <p>No metrics summary</p>}
              {host.last_error ? <p className="fleet-card-error">Last error: {host.last_error}</p> : null}
              <div className="fleet-card-actions">
                <button type="button" onClick={() => openHost(host)} disabled={!canOpen}>Manage</button>
                <button type="button" onClick={() => void refreshFleet()} disabled={!host.enabled || pendingHostId === host.id}>Refresh</button>
                <button type="button" onClick={() => void toggleEnabled(host)} disabled={pendingHostId === host.id}>
                  {host.enabled ? 'Disable' : 'Enable'}
                </button>
              </div>
              {!canOpen ? <p className="fleet-disabled-reason">Host workspace unavailable: {host.enabled ? `status is ${host.status}` : 'routing disabled'}.</p> : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
