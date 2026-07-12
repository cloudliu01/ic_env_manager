import { DiskSnapshot } from '../../features/metrics/types';
import { formatBytes, formatPercent } from './format';

export function DiskTable({ disks }: { disks: DiskSnapshot[] }) {
  if (!disks.length) {
    return <p className="monitoring-empty">No disk data reported.</p>;
  }

  return (
    <div className="monitoring-table-wrap">
      <table className="monitoring-table">
        <thead>
          <tr>
            <th>Mount</th>
            <th>Filesystem</th>
            <th>Used</th>
            <th>Total</th>
            <th>Usage</th>
          </tr>
        </thead>
        <tbody>
          {disks.map((disk) => (
            <tr key={`${disk.mount}-${disk.device ?? ''}`}>
              <td>{disk.mount}</td>
              <td>{disk.fstype || 'unknown'}</td>
              <td>{formatBytes(disk.used_bytes)}</td>
              <td>{formatBytes(disk.total_bytes)}</td>
              <td>
                <div className="metric-bar" aria-label={`${disk.mount} usage ${formatPercent(disk.percent)}`}>
                  <span style={{ width: `${Math.min(100, Math.max(0, disk.percent))}%` }} />
                </div>
                <span>{formatPercent(disk.percent)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
