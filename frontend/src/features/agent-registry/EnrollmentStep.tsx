import { EnrollmentJob } from './enrollment-api';

export function EnrollmentStep({ job, onCancel, onRetry }: { job: EnrollmentJob; onCancel: () => void; onRetry: () => void }) {
  const waiting = job.state === 'awaiting_cli';
  const automatic = job.state === 'running';
  const label = waiting ? 'Waiting for CLI' : automatic ? 'Automatic SSH enrollment' : job.state === 'verified' ? 'Verification complete' : `Enrollment ${job.state}`;
  const phases = Object.entries(job.preview?.phases ?? {});
  return <section className="detail-panel" aria-live="polite"><h2>{label}</h2>
    {automatic ? <p>Manager is completing the bounded SSH enrollment and Agent verification.</p> : null}
    {phases.length ? <ul>{phases.map(([name, phase]) => <li key={name}>{name}: {phase.status ?? 'pending'}{phase.code ? ` (${phase.code})` : ''}</li>)}</ul> : null}
    {waiting ? <><p>Complete the command on the Manager host:</p>{job.cli ? <code>{job.cli.display}</code> : <p role="alert">The Manager CLI socket is unavailable. Cancel this enrollment and enable the local enrollment socket.</p>}<p className="secondary-cell">The pending enrollment expires automatically. Nothing secret is displayed here.</p><button type="button" className="secondary-button" onClick={onCancel}>Cancel enrollment</button></> : null}
    {job.state === 'failed' ? <><p role="alert">Enrollment could not complete ({job.last_error_code ?? 'enrollment_failed'}). Review the failed phase and connection details.</p><button type="button" onClick={onRetry}>Retry enrollment</button></> : null}
    {job.state === 'expired' || job.state === 'cancelled' ? <><p role="alert">Enrollment {job.state} ({job.last_error_code ?? `agent_enrollment_${job.state}`}). Start a new enrollment when ready.</p><button type="button" onClick={onRetry}>Retry enrollment</button></> : null}
  </section>;
}
