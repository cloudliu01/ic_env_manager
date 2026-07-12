import { ChangeEvent } from 'react';

export type ConnectionValues = { baseUrl: string; displayName: string; profile: string; sshUser: string; sshHost: string; sshPort: string };

export function ConnectionStep({ values, errors, onChange, onBlur, locked }: { values: ConnectionValues; errors: Partial<Record<keyof ConnectionValues, string>>; onChange: (field: keyof ConnectionValues, value: string) => void; onBlur: (field: keyof ConnectionValues) => void; locked?: boolean }) {
  const input = (field: keyof ConnectionValues, label: string, helper: string, type = 'text') => <div className="form-field"><label htmlFor={field}>{label}</label><input id={field} type={type} value={values[field]} onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(field, event.target.value)} onBlur={() => onBlur(field)} aria-invalid={Boolean(errors[field])} aria-describedby={`${field}-help ${errors[field] ? `${field}-error` : ''}`} disabled={locked} /> <span id={`${field}-help`} className="secondary-cell">{helper}</span>{errors[field] ? <span id={`${field}-error`} className="field-error">{errors[field]}</span> : null}</div>;
  return <fieldset className="form-stack" disabled={locked}><legend>Step 1 of 3: Connection</legend>
    {input('displayName', 'Display name', 'A local name for this Agent.')}
    {input('baseUrl', 'Agent URL', 'The Agent endpoint Manager will verify.')}
    {input('profile', 'Transport profile', 'The Manager transport profile configured for this endpoint.')}
    {input('sshUser', 'SSH user', 'An existing Linux user allowed to run the fixed enrollment helper.')}
    {input('sshHost', 'SSH host', 'The SSH address for the same Agent host.')}
    {input('sshPort', 'SSH port', 'The SSH port; defaults to 22.', 'number')}
  </fieldset>;
}
