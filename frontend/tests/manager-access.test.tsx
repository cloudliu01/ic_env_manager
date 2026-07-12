import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ManagerAccessPage } from '../src/features/agent-settings/ManagerAccessPage';

afterEach(() => vi.unstubAllGlobals());

it('shows complete safe lifecycle metadata and confirms revocation', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'DELETE') {
      return new Response(JSON.stringify({ credential_id: 'cred-1', state: 'revoked' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({ credentials: [{
      credential_id: 'cred-1', manager_id: 'manager-a', state: 'active', pending_expires_at: null,
      created_at: '2026-07-01T09:00:00Z', activated_at: '2026-07-01T09:01:00Z',
      last_used_at: '2026-07-11T09:59:00Z', revoked_at: null,
    }, {
      credential_id: 'cred-2', manager_id: 'manager-pending', state: 'pending', pending_expires_at: '2026-07-12T11:30:00Z',
      created_at: '2026-07-12T11:00:00Z', activated_at: null, last_used_at: null, revoked_at: null,
    }, {
      credential_id: 'cred-3', manager_id: 'manager-revoked', state: 'revoked', pending_expires_at: null,
      created_at: '2026-06-01T09:00:00Z', activated_at: '2026-06-01T09:01:00Z',
      last_used_at: '2026-06-11T09:59:00Z', revoked_at: '2026-06-12T10:00:00Z',
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(<QueryClientProvider client={queryClient}><ManagerAccessPage /></QueryClientProvider>);

  expect(await screen.findByText('manager-a')).toBeTruthy();
  expect(screen.getByText('cred-1')).toBeTruthy();
  expect(screen.getByText(/Pending until/)).toBeTruthy();
  expect(screen.getByText(/Revoked/)).toBeTruthy();
  expect(screen.queryByText(/token|hash|ssh key/i)).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Revoke manager-a access' }));
  expect(screen.getByRole('dialog')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Confirm revoke' }));
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/v2/manager-credentials/cred-1'), expect.objectContaining({ method: 'DELETE' }));
});

it('traps dialog focus, closes with Escape, and restores the revoke trigger', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'DELETE') {
      return new Response(JSON.stringify({ credential_id: 'cred-1', state: 'revoked' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({ credentials: [{
      credential_id: 'cred-1', manager_id: 'manager-a', state: 'active', pending_expires_at: null,
      created_at: '2026-07-01T09:00:00Z', activated_at: '2026-07-01T09:01:00Z', last_used_at: null, revoked_at: null,
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(<QueryClientProvider client={queryClient}><ManagerAccessPage /></QueryClientProvider>);
  const trigger = await screen.findByRole('button', { name: 'Revoke manager-a access' });

  await user.click(trigger);
  const cancel = screen.getByRole('button', { name: 'Cancel' });
  const confirm = screen.getByRole('button', { name: 'Confirm revoke' });
  await waitFor(() => expect(document.activeElement).toBe(cancel));
  await user.keyboard('{Shift>}{Tab}{/Shift}');
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel revoke' }));
  await user.keyboard('{Shift>}{Tab}{/Shift}');
  expect(document.activeElement).toBe(confirm);
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel revoke' }));
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);

  await user.click(trigger);
  await user.click(screen.getByRole('button', { name: 'Confirm revoke' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(document.activeElement).toBe(trigger);
});
