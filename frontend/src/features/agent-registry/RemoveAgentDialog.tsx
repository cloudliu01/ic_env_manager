import { useState } from 'react';
import { removeAgent } from './enrollment-api';

function codeOf(error: unknown) { return typeof error === 'object' && error && 'code' in error && typeof error.code === 'string' ? error.code : ''; }

export function RemoveAgentDialog({ agentId, onClose, onRemoved }: { agentId: string; onClose: () => void; onRemoved: () => void }) {
  const [localOnly, setLocalOnly] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const remove = async () => { if (localOnly && !confirmed) { setError('Confirm that the remote credential may remain before local-only removal.'); return; } setSaving(true); setError(''); try { await removeAgent(agentId, localOnly); onRemoved(); } catch (failure) { setError(codeOf(failure) === 'agent_in_use' ? 'This Agent is currently in use. Close this dialog, stop the active operation, then try again.' : 'Removal could not complete. You can retry or close this dialog.'); } finally { setSaving(false); } };
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="remove-agent-title"><button type="button" className="dialog-close" onClick={onClose} aria-label="Close">×</button><h2 id="remove-agent-title">Remove from Manager</h2><p>This removes the Agent registration from Manager. Remote credential cleanup is requested when the Agent is reachable.</p>{error ? <p role="alert">{error}</p> : null}<label className="form-field"><input type="checkbox" checked={localOnly} onChange={(event) => setLocalOnly(event.target.checked)} /> Remove locally only<span className="secondary-cell">Use only when the Agent is offline; its remote credential may remain.</span></label>{localOnly ? <label className="form-field"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> I understand the remote credential may remain active.</label> : null}<div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose}>Close</button><button type="button" className="danger-button" onClick={() => void remove()} disabled={saving}>{saving ? 'Removing…' : 'Remove from Manager'}</button></div></section></div>;
}
