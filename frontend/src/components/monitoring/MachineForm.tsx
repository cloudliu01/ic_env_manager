import { FormEvent, useState } from 'react';
import { MachineCreateRequest } from '../../api/monitoring';

export function MachineForm({ onAdd }: { onAdd: (machine: MachineCreateRequest) => Promise<void> }) {
  const [name, setName] = useState('');
  const [address, setAddress] = useState('');
  const [port, setPort] = useState('8765');
  const [key, setKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await onAdd({ name: name || undefined, address, port: Number(port), key });
      setName('');
      setAddress('');
      setPort('8765');
      setKey('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="machine-form" onSubmit={submit}>
      <h3>Add monitored machine</h3>
      <label>
        Name
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Lab workstation" />
      </label>
      <label>
        Address
        <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="192.168.1.25" required />
      </label>
      <label>
        Port
        <input value={port} onChange={(event) => setPort(event.target.value)} type="number" min="1" max="65535" required />
      </label>
      <label>
        Key
        <input value={key} onChange={(event) => setKey(event.target.value)} type="password" placeholder="Bearer token" required />
      </label>
      {error ? <p role="alert" className="monitoring-error">{error}</p> : null}
      <button type="submit" disabled={submitting}>{submitting ? 'Adding…' : 'Add machine'}</button>
    </form>
  );
}
