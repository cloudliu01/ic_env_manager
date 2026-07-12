import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ConnectionStep, ConnectionValues } from './ConnectionStep';
import { EnrollmentStep } from './EnrollmentStep';
import { VerifySaveStep } from './VerifySaveStep';
import { cancelEnrollment, createEnrollment, validateLegacyAgent } from './enrollment-api';
import { useEnrollmentJob } from './enrollment-queries';
import { agentKeys } from './queries';
import { fleetKeys } from '../fleet/queries';
import { useDiscoveryResult } from '../discovery/queries';

const defaults: ConnectionValues = { displayName: '', baseUrl: '', profile: 'trusted-lan-http', sshUser: '', sshHost: '', sshPort: '22' };

function fieldError(field: keyof ConnectionValues, value: string) {
  if (field === 'displayName') return value.trim() ? '' : 'Display name is required.';
  if (field === 'baseUrl') try { const url = new URL(value); return /^https?:$/.test(url.protocol) ? '' : 'Use an HTTP or HTTPS URL.'; } catch { return 'Enter a complete Agent URL.'; }
  if (field === 'profile') return value.trim() ? '' : 'Transport profile is required.';
  if (field === 'sshUser' || field === 'sshHost') return value.trim() ? '' : 'This field is required.';
  return /^\d+$/.test(value) && Number(value) > 0 && Number(value) < 65536 ? '' : 'SSH port must be between 1 and 65535.';
}

export function AddAgentPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const discoveryResultId = params.get('discoveryResult') ?? undefined;
  const candidate = useDiscoveryResult(discoveryResultId);
  const [values, setValues] = useState<ConnectionValues>(defaults);
  const [errors, setErrors] = useState<Partial<Record<keyof ConnectionValues, string>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [legacy, setLegacy] = useState(false);
  const [legacyToken, setLegacyToken] = useState('');
  const enrollmentId = params.get('enrollment') ?? undefined;
  const job = useEnrollmentJob(enrollmentId);
  useEffect(() => {
    if (!candidate.data) return;
    const result = candidate.data.result;
    setValues((current) => ({ ...current, baseUrl: result.candidate_url, profile: result.transport_profile_id, sshHost: result.ip }));
  }, [candidate.data]);
  const cancelCurrent = async () => { if (!enrollmentId) return; try { await cancelEnrollment(enrollmentId); } finally { const next = new URLSearchParams(params); next.delete('enrollment'); setParams(next, { replace: true }); } };
  const change = (field: keyof ConnectionValues, value: string) => { if (enrollmentId && job.data?.state !== 'verified') void cancelCurrent(); setValues((current) => ({ ...current, [field]: value })); };
  const blur = (field: keyof ConnectionValues) => setErrors((current) => ({ ...current, [field]: fieldError(field, values[field]) }));
  const start = async () => {
    const nextErrors = Object.fromEntries((Object.keys(values) as Array<keyof ConnectionValues>).map((field) => [field, fieldError(field, values[field])]).filter(([, error]) => error));
    setErrors(nextErrors); setSubmitError(''); if (Object.keys(nextErrors).length) return;
    setSubmitting(true);
    try {
      const created = legacy ? await validateLegacyAgent({ base_url: values.baseUrl, transport_profile_id: values.profile, token: legacyToken }) : await createEnrollment({ base_url: values.baseUrl, display_name: values.displayName, transport_profile_id: values.profile, ssh: { user: values.sshUser, host: values.sshHost, port: Number(values.sshPort) }, ...(discoveryResultId ? { discovery_result_id: discoveryResultId } : {}) });
      const next = new URLSearchParams(params); next.set('enrollment', created.enrollment_id); setParams(next, { replace: true });
    } catch { setSubmitError('Enrollment could not start. Review the details and retry.'); } finally { if (legacy) { setLegacyToken(''); setLegacy(false); } setSubmitting(false); }
  };
  const onSaved = () => { void client.invalidateQueries({ queryKey: agentKeys.all }); void client.invalidateQueries({ queryKey: fleetKeys.all }); navigate('/fleet'); };
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Add agent</h1><p>Enroll an Agent with the Manager’s existing safe v2 flow.</p></header>
    <p className="transport-alert" role="alert">Trusted-LAN connection is unencrypted. Verify this Agent remains on the trusted LAN.</p>
    {discoveryResultId ? <p className="detail-panel">Discovery candidate selected. The opaque discovery result is fixed; confirm the SSH user before starting enrollment.</p> : null}
    {candidate.isError ? <p role="alert">Discovery candidate could not be restored. Return to Discovery and choose it again.</p> : null}
    {submitError || Object.values(errors).some(Boolean) ? <div role="alert"><p>{submitError || 'Correct the highlighted fields before continuing.'}</p></div> : null}
    <ConnectionStep values={values} errors={errors} onChange={change} onBlur={blur} locked={submitting} />
    {legacy ? <label className="form-field">Legacy admin token<input aria-label="Legacy admin token" type="password" value={legacyToken} onChange={(event) => setLegacyToken(event.target.value)} /><span className="secondary-cell">Compatibility-only validation. The token is sent once and is never shown again.</span></label> : <button type="button" className="secondary-button" onClick={() => setLegacy(true)}>Use legacy token instead</button>}
    <button type="button" onClick={() => void start()} disabled={submitting || Boolean(enrollmentId)}>{submitting ? 'Starting enrollment…' : legacy ? 'Validate legacy token' : 'Start enrollment'}</button>
    {job.isError ? <p role="alert">Enrollment status could not be refreshed. Reload this page using the current enrollment ID.</p> : null}
    {job.data ? <><EnrollmentStep job={job.data} onCancel={() => void cancelCurrent()} /><VerifySaveStep job={job.data} displayName={values.displayName} onSaved={onSaved} /></> : null}
    <p><Link to="/fleet">Return to Fleet</Link></p>
  </section>;
}
