import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Agent } from './types';
import { consumeCredentialRotation, startCredentialRotation, updateAgent } from './enrollment-api';
import { useEnrollmentJob } from './enrollment-queries';
import { agentKeys } from './queries';
import { fleetKeys } from '../fleet/queries';
import { RemoveAgentDialog } from './RemoveAgentDialog';
import { useTransportProfiles } from './transport-profiles';

export function EditAgentForm({ agent }: { agent: Agent }) {
  const client = useQueryClient();
  const transportProfiles = useTransportProfiles();
  const [displayName, setDisplayName] = useState(agent.display_name);
  const [enabled, setEnabled] = useState(agent.enabled);
  const [baseUrl, setBaseUrl] = useState(agent.endpoint ?? '');
  const [profile, setProfile] = useState(agent.transport_profile_id ?? '');
  const [verified, setVerified] = useState(false);
  const [legacyTokenRequired, setLegacyTokenRequired] = useState(false);
  const [legacyToken, setLegacyToken] = useState('');
  const [error, setError] = useState('');
  const [removeOpen, setRemoveOpen] = useState(false);
  const [rotation, setRotation] = useState('');
  const [rotationOpen, setRotationOpen] = useState(false);
  const [rotationSsh, setRotationSsh] = useState({ user: '', host: '', port: '22' });
  const [rotationId, setRotationId] = useState<string>();
  const removeTriggerRef = useRef<HTMLButtonElement>(null);
  const rotationJob = useEnrollmentJob(rotationId);
  const changedIdentity = baseUrl !== (agent.endpoint ?? '') || profile !== (agent.transport_profile_id ?? '');
  const save = async () => {
    if (changedIdentity && !verified) { setError('Confirm that the new endpoint is the same Agent identity before saving.'); return; }
    if (legacyTokenRequired && !legacyToken.trim()) { setError('Enter the legacy admin token to revalidate this endpoint.'); return; }
    setError('');
    try {
      await updateAgent(agent.agent_id, {
        display_name: displayName,
        enabled,
        ...(changedIdentity ? { base_url: baseUrl, transport_profile_id: profile } : {}),
        ...(legacyTokenRequired ? { legacy_token: legacyToken } : {}),
      });
      await client.invalidateQueries({ queryKey: agentKeys.detail(agent.agent_id) });
      await client.invalidateQueries({ queryKey: fleetKeys.all });
    } catch (caught) {
      if ((caught as { code?: string }).code === 'legacy_revalidation_required') {
        setLegacyTokenRequired(true);
        setError('This legacy Agent must be revalidated with its current admin token.');
      } else {
        setError('Changes could not be saved. Retry after checking the Agent connection.');
      }
    } finally {
      setLegacyToken('');
    }
  };
  const rotate = async () => { if (!rotationSsh.user.trim() || !rotationSsh.host.trim() || !/^\d+$/.test(rotationSsh.port) || Number(rotationSsh.port) < 1 || Number(rotationSsh.port) > 65535) { setRotation('Enter a valid SSH user, host, and port before starting credential rotation.'); return; } try { const result = await startCredentialRotation(agent.agent_id, { user: rotationSsh.user, host: rotationSsh.host, port: Number(rotationSsh.port) }); setRotationId(result.rotation.enrollment_id); setRotation('Credential rotation started.'); } catch { setRotation('Credential rotation could not start. Check SSH details and retry.'); } };
  const consumeRotation = async () => { if (!rotationId) return; try { const result = await consumeCredentialRotation(agent.agent_id, rotationId); setRotation(result.rotation.residual_warning ?? 'Credential rotation completed.'); } catch { setRotation('Credential rotation could not be applied. Retry after verification.'); } };
  const closeRemoveDialog = () => { setRemoveOpen(false); queueMicrotask(() => removeTriggerRef.current?.focus()); };
  const profiles = transportProfiles.data?.profiles ?? [{ id: 'system-tls', type: 'verified_tls' as const, security_label: 'Verified TLS', warning: null }];
  const selectedProfile = profiles.find((item) => item.id === profile);
  const showTransportWarning = selectedProfile?.warning === 'trusted_lan_http_unencrypted'
    || (profile === agent.transport_profile_id && agent.transport_warning === 'trusted_lan_http_unencrypted');
  return <section className="detail-panel"><h2>Edit registration</h2>{error ? <p role="alert">{error}</p> : null}<label className="form-field">Display name<input aria-label="Display name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} onBlur={() => setError(displayName.trim() ? '' : 'Display name is required.')} /><span className="secondary-cell">A local label for this Agent.</span></label><label className="form-field">Agent URL<input aria-label="Agent URL" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setVerified(false); setLegacyTokenRequired(false); setLegacyToken(''); }} /><span className="secondary-cell">Changing the endpoint verifies the same Agent identity.</span></label><label className="form-field">Transport profile<select aria-label="Transport profile" value={profile} onChange={(event) => { setProfile(event.target.value); setVerified(false); setLegacyTokenRequired(false); setLegacyToken(''); }}>{!profiles.some((item) => item.id === profile) ? <option value={profile}>{profile}</option> : null}{profiles.map((item) => <option key={item.id} value={item.id}>{item.security_label} — {item.id}</option>)}</select><span className="secondary-cell">Changing the profile verifies the same Agent identity.</span></label>{showTransportWarning ? <p className="transport-alert" role="alert">Trusted-LAN connection is unencrypted.</p> : null}<label className="form-field"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Enabled<span className="secondary-cell">Disabled Agents are retained but not probed.</span></label>{changedIdentity ? <label className="form-field"><input type="checkbox" checked={verified} onChange={(event) => setVerified(event.target.checked)} /> I verified this is the same Agent identity.</label> : null}{legacyTokenRequired ? <label className="form-field">Legacy admin token<input aria-label="Legacy admin token" type="password" autoComplete="off" value={legacyToken} onChange={(event) => setLegacyToken(event.target.value)} /><span className="secondary-cell">Legacy Agents have no stable instance identity. This one-time token validates the new endpoint and replaces the stored credential.</span></label> : null}<div className="form-actions"><button type="button" onClick={() => void save()}>Save changes</button><button type="button" className="secondary-button" onClick={() => setRotationOpen(true)}>Rotate credential</button><button ref={removeTriggerRef} type="button" className="danger-button" onClick={() => setRemoveOpen(true)}>Remove from Manager</button></div>{rotationOpen ? <section className="detail-panel"><h3>Credential rotation</h3><label className="form-field">Rotation SSH user<input aria-label="Rotation SSH user" value={rotationSsh.user} onChange={(event) => setRotationSsh((current) => ({ ...current, user: event.target.value }))} /></label><label className="form-field">Rotation SSH host<input aria-label="Rotation SSH host" value={rotationSsh.host} onChange={(event) => setRotationSsh((current) => ({ ...current, host: event.target.value }))} /></label><label className="form-field">Rotation SSH port<input aria-label="Rotation SSH port" type="number" value={rotationSsh.port} onChange={(event) => setRotationSsh((current) => ({ ...current, port: event.target.value }))} /></label>{rotationJob.data?.state === 'verified' ? <button type="button" onClick={() => void consumeRotation()}>Apply rotated credential</button> : <button type="button" onClick={() => void rotate()} disabled={Boolean(rotationId)}>Start credential rotation</button>}</section> : null}{rotation ? <p role="status">{rotation}</p> : null}{removeOpen ? <RemoveAgentDialog agentId={agent.agent_id} onClose={closeRemoveDialog} onRemoved={() => { void client.invalidateQueries({ queryKey: fleetKeys.all }); window.location.assign('/fleet'); }} /> : null}</section>;
}
