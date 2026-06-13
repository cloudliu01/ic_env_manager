import { useEffect, useMemo, useState } from 'react';
import { addMachine, deleteMachine, getMachineSnapshot, HostSnapshot, listMachines, MachineCreateRequest, MachineSummary } from '../api/monitoring';
import { DiskTable } from '../components/monitoring/DiskTable';
import { formatBytes, formatDuration, formatPercent } from '../components/monitoring/format';
import { MachineForm } from '../components/monitoring/MachineForm';
import { MachineSelector } from '../components/monitoring/MachineSelector';
import { MetricCard } from '../components/monitoring/MetricCard';

const REFRESH_MS = 5000;

export function MetricsPage() {
  const [machines, setMachines] = useState<MachineSummary[]>([]);
  const [selectedId, setSelectedId] = useState('local');
  const [snapshot, setSnapshot] = useState<HostSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const selectedMachine = useMemo(
    () => machines.find((machine) => machine.id === selectedId) ?? machines[0],
    [machines, selectedId],
  );

  async function loadMachines() {
    const items = await listMachines();
    setMachines(items);
    setSelectedId((current) => (items.some((machine) => machine.id === current) ? current : 'local'));
  }

  async function loadSnapshot(machineId = selectedId) {
    setLoading(true);
    setError(null);
    try {
      setSnapshot(await getMachineSnapshot(machineId));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMachines()
      .then(() => loadSnapshot('local'))
      .catch((err: Error) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!selectedId) {
      return;
    }
    void loadSnapshot(selectedId);
    const timer = window.setInterval(() => {
      void loadSnapshot(selectedId);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [selectedId]);

  async function handleAdd(machine: MachineCreateRequest) {
    const created = await addMachine(machine);
    await loadMachines();
    setSelectedId(created.id);
  }

  async function handleDelete(machineId: string) {
    await deleteMachine(machineId);
    await loadMachines();
    if (selectedId === machineId) {
      setSelectedId('local');
    }
  }

  const rootDisk = snapshot?.disks.find((disk) => disk.mount === '/') ?? snapshot?.disks[0];

  return (
    <section className="monitoring-page">
      <header className="monitoring-hero">
        <div>
          <p className="eyebrow">Host monitoring</p>
          <h2>Machine telemetry</h2>
          <p>Track CPU, memory, disk, uptime, and network activity across local and remote agents.</p>
        </div>
        <div className="monitoring-actions">
          <MachineSelector machines={machines} selectedId={selectedId} onSelect={setSelectedId} />
          <button type="button" onClick={() => void loadSnapshot(selectedId)}>Refresh</button>
        </div>
      </header>

      {error ? <p role="alert" className="monitoring-error">{error}</p> : null}

      <div className="monitoring-status-card">
        <div>
          <span className={`status-badge status-${snapshot?.status ?? 'loading'}`}>{snapshot?.status ?? 'loading'}</span>
          <strong>{snapshot?.name ?? selectedMachine?.name ?? 'Machine'}</strong>
          <span>{snapshot?.hostname ?? snapshot?.address ?? selectedMachine?.endpoint}</span>
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
          <h3>Disk usage</h3>
          <DiskTable disks={snapshot?.disks ?? []} />
        </article>
        <article className="monitoring-panel">
          <h3>Network interfaces</h3>
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

      <div className="monitoring-grid">
        <article className="monitoring-panel">
          <h3>Configured machines</h3>
          <div className="machine-list">
            {machines.map((machine) => (
              <div className="machine-row" key={machine.id}>
                <div>
                  <strong>{machine.name}</strong>
                  <span>{machine.is_local ? 'local agent' : machine.endpoint}</span>
                </div>
                <button type="button" disabled={machine.is_local} onClick={() => void handleDelete(machine.id)}>Delete</button>
              </div>
            ))}
          </div>
        </article>
        <article className="monitoring-panel">
          <MachineForm onAdd={handleAdd} />
        </article>
      </div>
    </section>
  );
}
