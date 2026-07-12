import { EnrollmentJob } from './enrollment-api';

export function EnrollmentStep({ job, onCancel }: { job: EnrollmentJob; onCancel: () => void }) {
  const waiting = job.state === 'awaiting_cli' || job.state === 'running';
  const label = waiting ? 'Waiting for CLI' : job.state === 'verified' ? 'Verification complete' : `Enrollment ${job.state}`;
  return <section className="detail-panel" aria-live="polite"><h2>{label}</h2>
    {waiting ? <><p>Complete the command on the Manager host:</p><code>ic-env-guardctl agent enroll</code><p className="secondary-cell">The pending enrollment expires automatically. Nothing secret is displayed here.</p><button type="button" className="secondary-button" onClick={onCancel}>Cancel enrollment</button></> : null}
    {job.state === 'failed' || job.state === 'expired' ? <p role="alert">Enrollment could not complete. Check the connection details and start a new enrollment.</p> : null}
  </section>;
}
