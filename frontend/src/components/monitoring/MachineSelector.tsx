import { MachineSummary } from '../../api/monitoring';

export function MachineSelector({
  machines,
  selectedId,
  onSelect,
}: {
  machines: MachineSummary[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <label className="machine-selector">
      Machine
      <select value={selectedId} onChange={(event) => onSelect(event.target.value)}>
        {machines.map((machine) => (
          <option key={machine.id} value={machine.id}>
            {machine.name} {machine.is_local ? '(local)' : `(${machine.endpoint})`}
          </option>
        ))}
      </select>
    </label>
  );
}
