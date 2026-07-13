import { ChangeEvent } from 'react';
import { TransportProfileOption } from './transport-profiles';

export type ConnectionValues = { baseUrl: string; displayName: string; profile: string; sshUser: string; sshHost: string; sshPort: string };

export function ConnectionStep({ values, errors, onChange, onBlur, profiles, locked, targetLocked }: { values: ConnectionValues; errors: Partial<Record<keyof ConnectionValues, string>>; onChange: (field: keyof ConnectionValues, value: string) => void; onBlur: (field: keyof ConnectionValues) => void; profiles: TransportProfileOption[]; locked?: boolean; targetLocked?: boolean }) {
  const input = (field: keyof ConnectionValues, label: string, helper: string, type = 'text') => <div className="form-field"><label htmlFor={field}>{label}</label><input id={field} type={type} value={values[field]} onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(field, event.target.value)} onBlur={() => onBlur(field)} aria-invalid={Boolean(errors[field])} aria-describedby={`${field}-help ${errors[field] ? `${field}-error` : ''}`} disabled={locked || (targetLocked && field !== 'displayName')} /> <span id={`${field}-help`} className="secondary-cell">{helper}</span>{errors[field] ? <span id={`${field}-error`} className="field-error">{errors[field]}</span> : null}</div>;
  return <fieldset className="form-stack" disabled={locked}><legend>Step 1 of 3: Connection</legend>
    {input('displayName', 'Display name', 'A local name for this Agent.')}
    {input('baseUrl', 'Agent URL', 'The Agent endpoint Manager will verify.')}
    <div className="form-field"><label htmlFor="profile">Transport profile</label><select id="profile" value={values.profile} onChange={(event) => onChange('profile', event.target.value)} onBlur={() => onBlur('profile')} aria-invalid={Boolean(errors.profile)} aria-describedby={`profile-help ${errors.profile ? 'profile-error' : ''}`} disabled={locked || targetLocked}>
      {!profiles.some((profile) => profile.id === values.profile) ? <option value={values.profile}>{values.profile}</option> : null}
      {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.security_label} — {profile.id}</option>)}
    </select><span id="profile-help" className="secondary-cell">Choose a transport profile configured by the Manager administrator.</span>{errors.profile ? <span id="profile-error" className="field-error">{errors.profile}</span> : null}</div>
    {input('sshUser', 'SSH user', 'An existing Linux user allowed to run the fixed enrollment helper.')}
    {input('sshHost', 'SSH host', 'The SSH address for the same Agent host.')}
    {input('sshPort', 'SSH port', 'The SSH port; defaults to 22.', 'number')}
  </fieldset>;
}
