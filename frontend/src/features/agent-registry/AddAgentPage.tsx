import { useEffect, useRef, useState } from 'react';
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
import { useTransportProfiles } from './transport-profiles';

const defaults: ConnectionValues = { displayName: '', baseUrl: '', profile: 'system-tls', sshUser: '', sshHost: '', sshPort: '22' };

function fieldError(field: keyof ConnectionValues, value: string) {
  if (field === 'displayName') return '';
  if (field === 'baseUrl') try { const url = new URL(value); return /^https?:$/.test(url.protocol) ? '' : 'Use an HTTP or HTTPS URL.'; } catch { return 'Enter a complete Agent URL.'; }
  if (field === 'profile') return value.trim() ? '' : 'Transport profile is required.';
  if (field === 'sshUser' || field === 'sshHost') return value.trim() ? '' : 'This field is required.';
  return /^\d+$/.test(value) && Number(value) > 0 && Number(value) < 65536 ? '' : 'SSH port must be between 1 and 65535.';
}

export function AddAgentPage({ sshEnrollmentAvailable = true }: { sshEnrollmentAvailable?: boolean }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const discoveryResultId = params.get('discoveryResult') ?? undefined;
  const candidate = useDiscoveryResult(discoveryResultId);
  const transportProfiles = useTransportProfiles();
  const [values, setValues] = useState<ConnectionValues>(defaults);
  const [errors, setErrors] = useState<Partial<Record<keyof ConnectionValues, string>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [legacy, setLegacy] = useState(!sshEnrollmentAvailable);
  const [legacyToken, setLegacyToken] = useState('');
  const derivedSshHost = useRef('');
  const enrollmentId = params.get('enrollment') ?? undefined;
  const job = useEnrollmentJob(enrollmentId);
  useEffect(() => {
    if (!candidate.data) return;
    const result = candidate.data.result;
    derivedSshHost.current = result.ip;
    setValues((current) => ({ ...current, baseUrl: result.candidate_url, profile: result.transport_profile_id, sshHost: result.ip }));
  }, [candidate.data]);
  const cancelCurrent = async () => { if (!enrollmentId) return; try { await cancelEnrollment(enrollmentId); } finally { const next = new URLSearchParams(params); next.delete('enrollment'); setParams(next, { replace: true }); } };
  const retryCurrent = () => { const next = new URLSearchParams(params); next.delete('enrollment'); setParams(next, { replace: true }); setSubmitError(''); };
  const change = (field: keyof ConnectionValues, value: string) => {
    if (enrollmentId && (field !== 'displayName' || Boolean(job.data && job.data.state !== 'verified'))) void cancelCurrent();
    setValues((current) => {
      if (field === 'baseUrl') {
        let hostname = '';
        try { hostname = new URL(value).hostname; } catch { /* keep the last valid derived host while typing */ }
        if (hostname && (!current.sshHost || current.sshHost === derivedSshHost.current)) {
          derivedSshHost.current = hostname;
          return { ...current, baseUrl: value, sshHost: hostname };
        }
      }
      if (field === 'sshHost' && value !== derivedSshHost.current) derivedSshHost.current = '';
      return { ...current, [field]: value };
    });
  };
  const blur = (field: keyof ConnectionValues) => setErrors((current) => ({ ...current, [field]: fieldError(field, values[field]) }));
  const start = async () => {
    const fields: Array<keyof ConnectionValues> = legacy ? ['baseUrl', 'profile'] : ['baseUrl', 'profile', 'sshUser', 'sshHost', 'sshPort'];
    const nextErrors = Object.fromEntries(fields.map((field) => [field, fieldError(field, values[field])]).filter(([, error]) => error));
    if (!selectedProfile) nextErrors.profile = 'Choose a transport profile loaded from this Manager.';
    setErrors(nextErrors); setSubmitError(legacy && !legacyToken.trim() ? 'Enter the legacy admin token.' : '');
    if (Object.keys(nextErrors).length || (legacy && !legacyToken.trim())) return;
    setSubmitting(true);
    try {
      const created = legacy ? await validateLegacyAgent({ base_url: values.baseUrl, transport_profile_id: values.profile, token: legacyToken }) : await createEnrollment({ base_url: values.baseUrl, ...(values.displayName.trim() ? { display_name: values.displayName.trim() } : {}), transport_profile_id: values.profile, ssh: { user: values.sshUser, host: values.sshHost, port: Number(values.sshPort) }, ...(discoveryResultId ? { discovery_result_id: discoveryResultId } : {}) });
      const next = new URLSearchParams(params); next.set('enrollment', created.enrollment_id); setParams(next, { replace: true });
    } catch { setSubmitError('Enrollment could not start. Review the details and retry.'); } finally { if (legacy) { setLegacyToken(''); setLegacy(false); } setSubmitting(false); }
  };
  const onSaved = () => { void client.invalidateQueries({ queryKey: agentKeys.all }); void client.invalidateQueries({ queryKey: fleetKeys.all }); navigate('/fleet'); };
  const profiles = transportProfiles.data?.profiles ?? [{ id: 'system-tls', type: 'verified_tls' as const, security_label: 'Verified TLS', warning: null }];
  const selectedProfile = profiles.find((profile) => profile.id === values.profile);
  const showTransportWarning = selectedProfile?.warning === 'trusted_lan_http_unencrypted'
    || (!selectedProfile && values.profile !== 'system-tls' && values.baseUrl.startsWith('http://'));
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Add agent</h1><p>Enroll an Agent with the Manager’s existing safe v2 flow.</p></header>
    {showTransportWarning ? <p className="transport-alert" role="alert">Trusted-LAN connection is unencrypted. Verify this Agent remains on the trusted LAN.</p> : null}
    {transportProfiles.isError ? <p role="alert">Configured transport profiles could not be loaded. Only the built-in Verified TLS profile is available.</p> : null}
    {discoveryResultId ? <p className="detail-panel">Discovery candidate selected. The opaque discovery result is fixed; confirm the SSH user before starting enrollment.</p> : null}
    {candidate.isError ? <p role="alert">Discovery candidate could not be restored. Return to Discovery and choose it again.</p> : null}
    {submitError || Object.values(errors).some(Boolean) ? <div role="alert"><p>{submitError || 'Correct the highlighted fields before continuing.'}</p></div> : null}
    {!sshEnrollmentAvailable ? <p role="status">SSH enrollment is unavailable on this Manager. Use the compatibility-only legacy token recovery path.</p> : null}
    <ConnectionStep values={values} errors={errors} onChange={change} onBlur={blur} profiles={profiles} locked={submitting} targetLocked={Boolean(enrollmentId && !job.data && !job.isError)} showSsh={!legacy} />
    {legacy ? <label className="form-field">Legacy admin token<input aria-label="Legacy admin token" type="password" value={legacyToken} onChange={(event) => setLegacyToken(event.target.value)} /><span className="secondary-cell">Compatibility-only validation. The token is sent once and is never shown again.</span></label> : <button type="button" className="secondary-button" onClick={() => { setLegacy(true); setErrors((current) => ({ ...current, sshUser: undefined, sshHost: undefined, sshPort: undefined })); }}>Use legacy token instead</button>}
    <button type="button" onClick={() => void start()} disabled={submitting || Boolean(enrollmentId) || !selectedProfile}>{submitting ? 'Starting enrollment…' : legacy ? 'Validate legacy token' : 'Start enrollment'}</button>
    {job.isError ? <p role="alert">Enrollment status could not be refreshed. Reload this page using the current enrollment ID.</p> : null}
    {job.data ? <><EnrollmentStep job={job.data} onCancel={() => void cancelCurrent()} onRetry={retryCurrent} /><VerifySaveStep job={job.data} displayName={values.displayName} onSaved={onSaved} /></> : null}
    <p><Link to="/fleet">Return to Fleet</Link></p>
  </section>;
}
