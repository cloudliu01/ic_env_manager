import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuditPage } from '../src/features/audit/AuditPage';

const listAgentAuditEvents = vi.hoisted(() => vi.fn());

vi.mock('../src/features/audit/api', () => ({ listAuditEvents: listAgentAuditEvents }));

describe('AuditPage', () => {
  beforeEach(() => {
    listAgentAuditEvents.mockResolvedValue({ events: [{ id: 2, timestamp: '2026-06-13T00:00:01Z', agent_id: 'local', operation: 'service.start', target_type: 'service', result: 'success' }] });
  });

  afterEach(() => cleanup());

  it('loads audit records for its explicit manager agent target', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AuditPage target={{ agentId: 'agent-b', name: 'Beta', capabilities: ['audit.v1'] }} /></QueryClientProvider>);

    expect(await screen.findByText('service.start')).toBeTruthy();
    expect(screen.queryByText('Gateway audit')).toBeNull();
    expect(listAgentAuditEvents).toHaveBeenCalledWith('agent-b', expect.anything(), expect.any(AbortSignal));
  });
});
