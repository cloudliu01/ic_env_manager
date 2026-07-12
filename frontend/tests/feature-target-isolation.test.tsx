import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { AuditPage } from '../src/features/audit/AuditPage';
import { LogsPage } from '../src/features/logs/LogsPage';
import { ObservationsPage } from '../src/features/observations/ObservationsPage';
import { MetricsPage } from '../src/features/metrics/MetricsPage';

const listAuditEvents = vi.hoisted(() => vi.fn());
const listLogs = vi.hoisted(() => vi.fn());
const tailLog = vi.hoisted(() => vi.fn());
const listObservations = vi.hoisted(() => vi.fn());
const getMonitoringSnapshot = vi.hoisted(() => vi.fn());

vi.mock('../src/features/audit/api', () => ({ listAuditEvents }));
vi.mock('../src/features/logs/api', () => ({ listLogs, tailLog }));
vi.mock('../src/features/observations/api', () => ({ listObservations }));
vi.mock('../src/features/metrics/api', () => ({ getMonitoringSnapshot }));

const target = (agentId: string, capability: string) => ({ agentId, name: agentId === 'agent-a' ? 'Alpha' : 'Beta', capabilities: [capability] });
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe('feature target isolation', () => {
  it('resets audit filters and opaque cursor before querying a warm target', async () => {
    listAuditEvents.mockImplementation(async (agentId: string) => ({ events: [], next_cursor: agentId === 'agent-a' ? 'opaque-a' : null }));
    const user = userEvent.setup();
    const queryClient = client();
    const { rerender } = render(<QueryClientProvider client={queryClient}><AuditPage target={target('agent-a', 'audit.v1')} /></QueryClientProvider>);
    await screen.findByRole('button', { name: 'Next page' });
    await user.type(screen.getByLabelText('Operation filter'), 'service.start');
    await user.type(screen.getByLabelText('Result filter'), 'failed');
    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => expect(listAuditEvents).toHaveBeenCalledWith('agent-a', expect.objectContaining({ cursor: 'opaque-a', operation: 'service.start', result: 'failed' }), expect.anything()));

    rerender(<QueryClientProvider client={queryClient}><AuditPage target={target('agent-b', 'audit.v1')} /></QueryClientProvider>);
    await waitFor(() => expect(listAuditEvents).toHaveBeenCalledWith('agent-b', { operation: undefined, result: undefined, cursor: undefined }, expect.anything()));
  });

  it('does not tail a selected log from the prior warm target', async () => {
    listLogs.mockImplementation(async (agentId: string) => [{ id: `${agentId}-log`, path: '/tmp/log', last_updated: '2026-07-12T00:00:00Z', observed_at: '', ttl_seconds: 1, received_at: '', expires_at: '', producer_id: '', updated_at: '', stale: false }]);
    tailLog.mockResolvedValue({ id: 'agent-a-log', path: '/tmp/log', lines: [], line_count: 0, truncated: false, last_updated: '' });
    const user = userEvent.setup();
    const queryClient = client();
    const { rerender } = render(<QueryClientProvider client={queryClient}><LogsPage target={target('agent-a', 'logs.v2')} /></QueryClientProvider>);
    await user.click(await screen.findByRole('button', { name: 'Tail agent-a-log' }));
    await waitFor(() => expect(tailLog).toHaveBeenCalledWith('agent-a', 'agent-a-log', expect.anything()));

    rerender(<QueryClientProvider client={queryClient}><LogsPage target={target('agent-b', 'logs.v2')} /></QueryClientProvider>);
    await screen.findByRole('button', { name: 'Tail agent-b-log' });
    expect(tailLog).not.toHaveBeenCalledWith('agent-b', 'agent-a-log', expect.anything());
  });

  it('does not retain an expanded observation across a warm target switch', async () => {
    listObservations.mockImplementation(async (agentId: string) => ({ items: [{ identity_key: 'same-key', namespace: 'health', name: 'daemon', kind: 'gauge', value: 1, unit: null, status: 'ok', message: null, labels: {}, details: { owner: agentId }, observed_at: '2026-07-12T00:00:00Z', ttl_seconds: 60, received_at: '', expires_at: '2026-07-12T00:01:00Z', producer_id: '', updated_at: '', stale: false }], next_cursor: null }));
    const user = userEvent.setup();
    const queryClient = client();
    const { rerender } = render(<BrowserRouter><QueryClientProvider client={queryClient}><ObservationsPage target={target('agent-a', 'observations.v2')} /></QueryClientProvider></BrowserRouter>);
    await user.click(await screen.findByRole('button', { name: 'Show details for health / daemon' }));
    expect(screen.getByText(/"owner": "agent-a"/)).toBeTruthy();

    rerender(<BrowserRouter><QueryClientProvider client={queryClient}><ObservationsPage target={target('agent-b', 'observations.v2')} /></QueryClientProvider></BrowserRouter>);
    await screen.findByRole('button', { name: 'Show details for health / daemon' });
    expect(screen.queryByLabelText('Observation details')).toBeNull();
  });

  it('requests and displays metrics for the new warm target only', async () => {
    getMonitoringSnapshot.mockImplementation(async (agentId: string) => ({ host_id: agentId, name: agentId === 'agent-a' ? 'Alpha telemetry' : 'Beta telemetry', address: agentId, status: 'online', sampled_at: '2026-07-12T00:00:00Z', cpu: { percent: 1, cores_logical: 1, cores_physical: 1, load_average: [] }, memory: { used_bytes: 1, total_bytes: 2, percent: 50 }, swap: { used_bytes: 0, total_bytes: 0, percent: 0 }, disks: [], network: [], uptime_seconds: 1 }));
    const queryClient = client();
    const { rerender } = render(<QueryClientProvider client={queryClient}><MetricsPage target={target('agent-a', 'monitoring.snapshot.v1')} /></QueryClientProvider>);
    expect(await screen.findByText('Alpha telemetry')).toBeTruthy();
    rerender(<QueryClientProvider client={queryClient}><MetricsPage target={target('agent-b', 'monitoring.snapshot.v1')} /></QueryClientProvider>);
    expect(await screen.findByText('Beta telemetry')).toBeTruthy();
    expect(screen.queryByText('Alpha telemetry')).toBeNull();
    expect(getMonitoringSnapshot).toHaveBeenCalledWith('agent-b', expect.anything());
  });
});
