import { DiskTable } from '../../components/monitoring/DiskTable';
import { MetricCard } from '../../components/monitoring/MetricCard';
import { formatBytes, formatDuration, formatPercent } from '../../components/monitoring/format';
import { useMonitoringSnapshot } from './queries';
import { MetricsTarget } from './types';

export function MetricsPage({ target }: { target: MetricsTarget }) {
  const supported = target.capabilities.includes('monitoring.snapshot.v1');
  const snapshot = useMonitoringSnapshot(target.agentId, supported);
  const refresh = snapshot.refetch;
  const data = snapshot.data;
  const rootDisk = data?.disks.find((disk) => disk.mount === '/') ?? data?.disks[0];
  return <section className="monitoring-page">
    <header className="monitoring-hero"><div><p className="eyebrow">Host monitoring</p><h1 tabIndex={-1}>Machine telemetry</h1><p>Track CPU, memory, disk, uptime, and network activity for this Agent.</p></div>
      <div className="monitoring-actions"><span className="machine-selector">Agent: {target.name}</span><button type="button" disabled={!supported} onClick={() => void refresh()}>Refresh</button></div>
    </header>
    {!supported ? <p role="status" className="monitoring-empty">This Agent does not support monitoring.</p> : null}
    {snapshot.isError ? <p role="alert" className="monitoring-error">{snapshot.error.message}</p> : null}
    <div className="monitoring-status-card"><div><span className={`status-badge status-${data?.status ?? 'loading'}`}>{data?.status ?? 'loading'}</span><strong>{data?.name ?? target.name}</strong><span>{data?.hostname ?? data?.address ?? target.agentId}</span></div><div><span>Sampled</span><strong>{data ? new Date(data.sampled_at).toLocaleTimeString() : snapshot.isPending ? 'Loading…' : 'Unavailable'}</strong></div></div>
    {data?.error ? <p role="alert" className="monitoring-error">{data.error}</p> : null}
    <div className="metric-grid"><MetricCard label="CPU" value={data ? formatPercent(data.cpu.percent) : '—'} detail={`${data?.cpu.cores_logical ?? 0} logical cores`} /><MetricCard label="Memory" value={data ? formatPercent(data.memory.percent) : '—'} detail={data ? `${formatBytes(data.memory.used_bytes)} / ${formatBytes(data.memory.total_bytes)}` : undefined} /><MetricCard label="Root disk" value={rootDisk ? formatPercent(rootDisk.percent) : '—'} detail={rootDisk ? `${formatBytes(rootDisk.used_bytes)} / ${formatBytes(rootDisk.total_bytes)}` : undefined} /><MetricCard label="Uptime" value={data ? formatDuration(data.uptime_seconds) : '—'} detail={data?.cpu.load_average.length ? `load ${data.cpu.load_average.map((value) => value.toFixed(2)).join(', ')}` : 'load unavailable'} /></div>
    <div className="monitoring-grid"><article className="monitoring-panel"><h2>Disk usage</h2><DiskTable disks={data?.disks ?? []} /></article><article className="monitoring-panel"><h2>Network interfaces</h2>{data?.network.length ? <div className="monitoring-table-wrap"><table className="monitoring-table"><thead><tr><th>Interface</th><th>RX</th><th>TX</th></tr></thead><tbody>{data.network.map((row) => <tr key={row.interface}><td>{row.interface}</td><td>{formatBytes(row.rx_bytes)}</td><td>{formatBytes(row.tx_bytes)}</td></tr>)}</tbody></table></div> : <p className="monitoring-empty">No network data reported.</p>}</article></div>
  </section>;
}
