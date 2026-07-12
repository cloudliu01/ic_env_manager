import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { LogsPage } from '../src/features/logs/LogsPage';

afterEach(() => vi.unstubAllGlobals());

it('requests a stable log ID with 100 lines and explicitly reports truncation', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.includes('/tail')) {
      return new Response(JSON.stringify({
        id: 'scheduler-log', path: '/var/log/scheduler.log', lines: ['line 99', 'line 100'],
        line_count: 2, truncated: true, last_updated: '2026-07-11T10:00:00Z',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({ items: [{
      id: 'scheduler-log', path: '/var/log/scheduler.log', last_updated: '2026-07-11T10:00:00Z',
      observed_at: '2026-07-11T10:00:00Z', ttl_seconds: 120, received_at: '2026-07-11T10:00:00Z',
      expires_at: '2026-07-11T10:02:00Z', producer_id: 'watcher', updated_at: '2026-07-11T10:00:00Z', stale: false,
    }] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  render(<QueryClientProvider client={queryClient}><LogsPage target={{ agentId: 'local', name: 'Build node', capabilities: ['logs.v2'] }} /></QueryClientProvider>);

  await user.click(await screen.findByRole('button', { name: 'Tail scheduler-log' }));
  expect(await screen.findByText('Output truncated to the latest 100 lines.')).toBeTruthy();
  expect(screen.getByText(/line 100/)).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/v2/logs/scheduler-log/tail?lines=100'), expect.anything());
});
