import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Agent } from './types';
import { startCredentialRotation, updateAgent } from './enrollment-api';
import { agentKeys } from './queries';
import { fleetKeys } from '../fleet/queries';
import { RemoveAgentDialog } from './RemoveAgentDialog';

export function EditAgentForm({ agent }: { agent: Agent }) {
  const client = useQueryClient();
  const [displayName, setDisplayName] = useState(agent.display_name);
  const [enabled, setEnabled] = useState(agent.enabled);
  const [baseUrl, setBaseUrl] = useState(agent.endpoint ?? '');
  const [profile, setProfile] = useState(agent.transport_profile_id ?? '');
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState('');
  const [removeOpen, setRemoveOpen] = useState(false);
  const [rotation, setRotation] = useState('');
  const changedIdentity = baseUrl !== (agent.endpoint ?? '') || profile !== (agent.transport_profile_id ?? '');
  const save = async () => { if (changedIdentity && !verified) { setError('Confirm that the new endpoint is the same Agent identity before saving.'); return; } setError(''); try { await updateAgent(agent.agent_id, { display_name: displayName, enabled, ...(changedIdentity ? { base_url: baseUrl, transport_profile_id: profile } : {}) }); await client.invalidateQueries({ queryKey: agentKeys.detail(agent.agent_id) }); await client.invalidateQueries({ queryKey: fleetKeys.all }); } catch { setError('Changes could not be saved. Retry after checking the Agent connection.'); } };
  const rotate = async () => { try { const result = await startCredentialRotation(agent.agent_id, { user: '', host: '', port: 22 }); setRotation(result.rotation.residual_warning ?? 'Credential rotation started. Keep the prior credential until Manager reports cleanup.'); } catch { setRotation('Credential rotation could not start. Check SSH details and retry.'); } };
  return <section className="detail-panel"><h2>Edit registration</h2>{error ? <p role="alert">{error}</p> : null}<label className="form-field">Display name<input aria-label="Display name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} onBlur={() => setError(displayName.trim() ? '' : 'Display name is required.')} /><span className="secondary-cell">A local label for this Agent.</span></label><label className="form-field">Agent URL<input aria-label="Agent URL" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setVerified(false); }} /><span className="secondary-cell">Changing the endpoint verifies the same Agent identity.</span></label><label className="form-field">Transport profile<input aria-label="Transport profile" value={profile} onChange={(event) => { setProfile(event.target.value); setVerified(false); }} /><span className="secondary-cell">Changing the profile verifies the same Agent identity.</span></label><label className="form-field"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Enabled<span className="secondary-cell">Disabled Agents are retained but not probed.</span></label>{changedIdentity ? <label className="form-field"><input type="checkbox" checked={verified} onChange={(event) => setVerified(event.target.checked)} /> I verified this is the same Agent identity.</label> : null}<div className="form-actions"><button type="button" onClick={() => void save()}>Save changes</button><button type="button" className="secondary-button" onClick={() => void rotate()}>Rotate credential</button><button type="button" className="danger-button" onClick={() => setRemoveOpen(true)}>Remove from Manager</button></div>{rotation ? <p role="status">{rotation}</p> : null}{removeOpen ? <RemoveAgentDialog agentId={agent.agent_id} onClose={() => setRemoveOpen(false)} onRemoved={() => { void client.invalidateQueries({ queryKey: fleetKeys.all }); window.location.assign('/fleet'); }} /> : null}</section>;
}
