import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ObservationsPage } from '../src/features/observations/ObservationsPage';

afterEach(() => vi.unstubAllGlobals());

it('renders dense observation columns and expands details outside the table', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
    items: [{
      identity_key: 'a'.repeat(64), namespace: 'eda', name: 'license-daemon', kind: 'gauge',
      value: 1, unit: 'process', status: 'warning', message: 'restart pending', labels: {},
      details: { pid: 4321, owner: 'eda' }, observed_at: '2026-07-11T10:00:00Z',
      ttl_seconds: 120, received_at: '2026-07-11T10:00:01Z', expires_at: '2026-07-11T10:02:00Z',
      producer_id: 'audit-script', updated_at: '2026-07-11T10:00:01Z', stale: false,
    }],
    next_cursor: null,
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(<BrowserRouter><QueryClientProvider client={queryClient}><ObservationsPage target={{ agentId: 'local', name: 'Build node', capabilities: ['observations.v2'] }} /></QueryClientProvider></BrowserRouter>);

  expect(await screen.findByText('eda / license-daemon')).toBeTruthy();
  expect(screen.getByText('Warning')).toBeTruthy();
  expect(screen.queryByText(/4321/)).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Show details for eda / license-daemon' }));
  expect(screen.getByText(/"pid": 4321/)).toBeTruthy();
});
