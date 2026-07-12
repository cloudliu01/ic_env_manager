import { ShieldCheck, ShieldX, X } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyboardEvent, useEffect, useRef, useState } from 'react';
import { listManagerCredentials, ManagerCredential, revokeManagerCredential } from './api';

function timestamp(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—';
}

function lifecycle(credential: ManagerCredential): string {
  if (credential.state === 'pending') {
    return `Pending until ${timestamp(credential.pending_expires_at)}`;
  }
  const active = `Activated ${timestamp(credential.activated_at)}; last used ${timestamp(credential.last_used_at)}`;
  return credential.state === 'revoked' ? `${active}; Revoked ${timestamp(credential.revoked_at)}` : active;
}

export function ManagerAccessPage() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ['agent', 'local', 'manager-credentials'], queryFn: ({ signal }) => listManagerCredentials(signal) });
  const [candidate, setCandidate] = useState<ManagerCredential | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const wasOpen = useRef(false);
  const mutation = useMutation({
    mutationFn: revokeManagerCredential,
    onSuccess: () => {
      setCandidate(null);
      void queryClient.invalidateQueries({ queryKey: ['agent', 'local', 'manager-credentials'] });
    },
  });

  useEffect(() => {
    if (candidate) {
      wasOpen.current = true;
      cancelRef.current?.focus();
      return;
    }
    if (wasOpen.current) {
      wasOpen.current = false;
      const target = triggerRef.current?.isConnected ? triggerRef.current : headingRef.current;
      target?.focus();
    }
  }, [candidate]);

  function openDialog(credential: ManagerCredential, trigger: HTMLButtonElement) {
    triggerRef.current = trigger;
    mutation.reset();
    setCandidate(credential);
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      setCandidate(null);
      return;
    }
    if (event.key !== 'Tab') {
      return;
    }
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  return <section className="feature-page">
    <header className="page-header"><div><h1 ref={headingRef} tabIndex={-1}>Manager Access</h1><p>Manager-specific credentials authorized to access this Agent.</p></div></header>
    {query.isPending ? <p role="status">Loading manager access…</p> : null}
    {query.isError ? <p role="alert">Unable to load manager access.</p> : null}
    {query.data?.length === 0 ? <p className="empty-state">No Manager credentials are registered.</p> : null}
    {query.data?.length ? <div className="table-region" tabIndex={0} aria-label="Manager access table, horizontally scrollable"><table>
      <thead><tr><th scope="col">Manager ID</th><th scope="col">Credential ID</th><th scope="col">State</th><th scope="col">Created</th><th scope="col">Lifecycle</th><th scope="col">Action</th></tr></thead>
      <tbody>{query.data.map((credential) => <tr key={credential.credential_id}>
        <th scope="row">{credential.manager_id}</th>
        <td className="data-cell">{credential.credential_id}</td>
        <td><span className={`status status-${credential.state}`}>{credential.state === 'active' ? <ShieldCheck size={16} aria-hidden="true" /> : <ShieldX size={16} aria-hidden="true" />}{credential.state}</span></td>
        <td className="data-cell">{timestamp(credential.created_at)}</td>
        <td className="data-cell">{lifecycle(credential)}</td>
        <td><button type="button" className="danger-button" disabled={credential.state === 'revoked'} onClick={(event) => openDialog(credential, event.currentTarget)}>Revoke {credential.manager_id} access</button></td>
      </tr>)}</tbody>
    </table></div> : null}
    {candidate ? <div className="dialog-backdrop"><div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="revoke-title" className="dialog" onKeyDown={handleDialogKeyDown}>
      <button type="button" className="icon-button dialog-close" aria-label="Cancel revoke" onClick={() => setCandidate(null)}><X aria-hidden="true" /></button>
      <h2 id="revoke-title">Revoke Manager access?</h2>
      <p><strong>{candidate.manager_id}</strong> will lose access through credential <code>{candidate.credential_id}</code>.</p>
      {mutation.isError ? <p role="alert">Revocation failed. The credential is unchanged.</p> : null}
      <div className="dialog-actions"><button ref={cancelRef} type="button" className="secondary-button" onClick={() => setCandidate(null)}>Cancel</button><button type="button" className="danger-button" disabled={mutation.isPending} onClick={() => mutation.mutate(candidate.credential_id)}>Confirm revoke</button></div>
    </div></div> : null}
  </section>;
}
