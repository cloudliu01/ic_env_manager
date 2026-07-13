import { useState } from 'react';
import { EnrollmentJob, saveEnrolledAgent } from './enrollment-api';

export function VerifySaveStep({ job, displayName, onSaved }: { job: EnrollmentJob; displayName: string; onSaved: () => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const agent = job.preview?.agent;
  const effectiveName = displayName.trim() || agent?.name?.trim() || '';
  const save = async () => { if (!effectiveName) return; setSaving(true); setError(''); try { await saveEnrolledAgent(job.enrollment_id, effectiveName); onSaved(); } catch { setError('The verified Agent could not be saved. You can retry safely.'); } finally { setSaving(false); } };
  if (job.state !== 'verified') return null;
  const warnings = Object.entries(job.preview?.phases ?? {}).filter(([, phase]) => phase.status === 'warning');
  const observations = agent?.summary?.observations as Record<string, unknown> | undefined;
  const services = agent?.summary?.services as Record<string, unknown> | undefined;
  const transport = agent?.transport_security === 'verified_tls' ? 'Verified TLS' : agent?.transport_security === 'trusted_lan_http' ? 'Trusted-LAN HTTP (unencrypted)' : 'Unknown transport';
  return <section className="detail-panel"><h2>Step 3 of 3: Verify &amp; Save Agent</h2>
    {agent ? <dl>
      <dt>Name</dt><dd>{agent.name ?? 'Unavailable'}</dd>
      <dt>Agent ID</dt><dd>{agent.instance_id ?? agent.agent_id ?? 'Unavailable'}</dd>
      <dt>Endpoint</dt><dd>{agent.endpoint ?? 'Unavailable'}</dd>
      <dt>Transport</dt><dd>{transport}</dd>
      <dt>Agent version</dt><dd>{agent.agent_version ?? 'Unavailable'}</dd>
      <dt>API version</dt><dd>{agent.api_version ?? 'Unavailable'}</dd>
      <dt>Capabilities</dt><dd>{agent.capabilities?.length ? <ul>{agent.capabilities.map((capability) => <li key={capability}>{capability}</li>)}</ul> : 'None reported'}</dd>
      <dt>Observations</dt><dd>{typeof observations?.critical === 'number' ? `${observations.critical} critical` : 'Unavailable'}</dd>
      <dt>Services</dt><dd>{typeof services?.unhealthy === 'number' ? `${services.unhealthy} unhealthy` : 'Unavailable'}</dd>
    </dl> : <p role="alert">Verified Agent details are unavailable. Retry verification before saving.</p>}
    {warnings.length ? <p role="status">Verification warning: {warnings.map(([name, phase]) => `${name}${phase.code ? ` (${phase.code})` : ''}`).join(', ')}.</p> : null}
    {!effectiveName ? <p role="alert">Enter a display name because this Agent did not report one.</p> : null}
    {error ? <p role="alert">{error}</p> : null}<button type="button" onClick={() => void save()} disabled={saving || !effectiveName || !agent}>{saving ? 'Saving…' : 'Save Agent'}</button></section>;
}
