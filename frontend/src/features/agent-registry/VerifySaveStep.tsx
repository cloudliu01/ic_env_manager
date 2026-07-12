import { useState } from 'react';
import { EnrollmentJob, saveEnrolledAgent } from './enrollment-api';

export function VerifySaveStep({ job, displayName, onSaved }: { job: EnrollmentJob; displayName: string; onSaved: () => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const save = async () => { setSaving(true); setError(''); try { await saveEnrolledAgent(job.enrollment_id, displayName); onSaved(); } catch { setError('The verified Agent could not be saved. You can retry safely.'); } finally { setSaving(false); } };
  if (job.state !== 'verified') return null;
  return <section className="detail-panel"><h2>Step 3 of 3: Save Agent</h2><p>Identity verification is complete. Save this Agent once to add it to Manager.</p>{error ? <p role="alert">{error}</p> : null}<button type="button" onClick={() => void save()} disabled={saving}>{saving ? 'Saving…' : 'Save Agent'}</button></section>;
}
