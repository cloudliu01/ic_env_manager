import { useEffect, useRef, useState } from 'react';
import { getAgentMonitoringSnapshot, HostSnapshot } from '../api/monitoring';
import { supportsCapability, useStandaloneAgent } from '../agents/StandaloneAgentContext';
import { DiskTable } from '../components/monitoring/DiskTable';
import { formatBytes, formatDuration, formatPercent } from '../components/monitoring/format';
import { MetricCard } from '../components/monitoring/MetricCard';

const REFRESH_MS = 5000;

export function MetricsPage() {
  const { agentId, name, capabilities } = useStandaloneAgent();
  const supportsMonitoring = supportsCapability(capabilities, 'monitoring.snapshot.v1');
  const [snapshot, setSnapshot] = useState<HostSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestGeneration = useRef(0);

  async function loadSnapshot(signal?: AbortSignal) {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    if (!supportsMonitoring) {
      setSnapshot(null);
      setError(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const nextSnapshot = await getAgentMonitoringSnapshot(agentId, signal ? { signal } : undefined);
      if (requestGeneration.current === generation) {
        setSnapshot(nextSnapshot);
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError' && requestGeneration.current === generation) {
        setSnapshot(null);
        setError((err as Error).message);
      }
    } finally {
      if (requestGeneration.current === generation) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadSnapshot(controller.signal);
    const timer = window.setInterval(() => {
      void loadSnapshot();
    }, REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [agentId, supportsMonitoring]);

  const rootDisk = snapshot?.disks.find((disk) => disk.mount === '/') ?? snapshot?.disks[0];

  return (
    <section className="monitoring-page">
      <header className="monitoring-hero">
        <div>
          <p className="eyebrow">Host monitoring</p>
          <h1 tabIndex={-1}>Machine telemetry</h1>
          <p>Track CPU, memory, disk, uptime, and network activity for the selected agent.</p>
        </div>
        <div className="monitoring-actions">
          <span className="machine-selector">Agent: {name}</span>
          <button type="button" disabled={!supportsMonitoring} onClick={() => void loadSnapshot()}>Refresh</button>
        </div>
      </header>

      {!supportsMonitoring ? <p role="status" className="monitoring-empty">This Agent does not support monitoring.</p> : null}
      {error ? <p role="alert" className="monitoring-error">{error}</p> : null}

      <div className="monitoring-status-card">
        <div>
          <span className={`status-badge status-${snapshot?.status ?? 'loading'}`}>{snapshot?.status ?? 'loading'}</span>
          <strong>{snapshot?.name ?? name}</strong>
          <span>{snapshot?.hostname ?? snapshot?.address ?? agentId}</span>
        </div>
        <div>
          <span>Sampled</span>
          <strong>{snapshot ? new Date(snapshot.sampled_at).toLocaleTimeString() : loading ? 'Loading…' : 'Unavailable'}</strong>
        </div>
      </div>

      {snapshot?.error ? <p role="alert" className="monitoring-error">{snapshot.error}</p> : null}

      <div className="metric-grid">
        <MetricCard label="CPU" value={snapshot ? formatPercent(snapshot.cpu.percent) : '—'} detail={`${snapshot?.cpu.cores_logical ?? 0} logical cores`} />
        <MetricCard label="Memory" value={snapshot ? formatPercent(snapshot.memory.percent) : '—'} detail={snapshot ? `${formatBytes(snapshot.memory.used_bytes)} / ${formatBytes(snapshot.memory.total_bytes)}` : undefined} />
        <MetricCard label="Root disk" value={rootDisk ? formatPercent(rootDisk.percent) : '—'} detail={rootDisk ? `${formatBytes(rootDisk.used_bytes)} / ${formatBytes(rootDisk.total_bytes)}` : undefined} />
        <MetricCard label="Uptime" value={snapshot ? formatDuration(snapshot.uptime_seconds) : '—'} detail={snapshot?.cpu.load_average.length ? `load ${snapshot.cpu.load_average.map((value) => value.toFixed(2)).join(', ')}` : 'load unavailable'} />
      </div>

      <div className="monitoring-grid">
        <article className="monitoring-panel">
          <h2>Disk usage</h2>
          <DiskTable disks={snapshot?.disks ?? []} />
        </article>
        <article className="monitoring-panel">
          <h2>Network interfaces</h2>
          {snapshot?.network.length ? (
            <div className="monitoring-table-wrap">
              <table className="monitoring-table">
                <thead>
                  <tr><th>Interface</th><th>RX</th><th>TX</th></tr>
                </thead>
                <tbody>
                  {snapshot.network.map((row) => (
                    <tr key={row.interface}>
                      <td>{row.interface}</td>
                      <td>{formatBytes(row.rx_bytes)}</td>
                      <td>{formatBytes(row.tx_bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="monitoring-empty">No network data reported.</p>}
        </article>
      </div>

      <article className="monitoring-panel">
        <h2>Selected agent</h2>
        <p className="monitoring-empty">Agent credentials are managed by the control plane configuration.</p>
      </article>
    </section>
  );
}
