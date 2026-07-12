import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuditStatusPage } from '../src/pages/AuditStatusPage';

const activeAgent = vi.hoisted(() => ({ id: 'agent-a' as string | null }));
const listGatewayAuditEvents = vi.hoisted(() => vi.fn());
const listAgentAuditEvents = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/agents/AgentStateContext', () => ({
  useActiveAgent: () => ({
    activeAgentId: activeAgent.id,
    activeAgent: activeAgent.id ? { id: activeAgent.id, name: 'Alpha agent', enabled: true, status: 'ready', capabilities: CAPABILITIES } : null,
  }),
  agentSupports: (agent: { capabilities?: string[] } | null, capability: string) => Boolean(agent?.capabilities?.includes(capability)),
}));

vi.mock('../src/api/audit', () => ({
  listGatewayAuditEvents,
  listAgentAuditEvents,
}));

describe('AuditStatusPage', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    activeAgent.id = 'agent-a';
    listGatewayAuditEvents.mockReset();
    listAgentAuditEvents.mockReset();
    listGatewayAuditEvents.mockResolvedValue({
      events: [
        {
          id: 1,
          timestamp: '2026-06-13T00:00:00Z',
          agent_id: 'agent-a',
          operation: 'gateway.service.start',
          target_type: 'service',
          target_id: 'demo',
          result: 'success',
          correlation_id: 'corr-1',
        },
      ],
    });
    listAgentAuditEvents.mockResolvedValue({
      events: [
        {
          id: 2,
          timestamp: '2026-06-13T00:00:01Z',
          agent_id: 'agent-a',
          operation: 'service.start',
          target_type: 'service',
          target_id: 'demo',
          result: 'success',
        },
      ],
    });
  });

  it('renders gateway and active-agent audit records without secret fields', async () => {
    render(<AuditStatusPage />);

    expect(await screen.findByText('Gateway audit')).toBeTruthy();
    expect(screen.getByText('Agent audit')).toBeTruthy();
    expect(await screen.findByText('gateway.service.start')).toBeTruthy();
    expect(await screen.findByText('service.start')).toBeTruthy();
    expect(screen.getAllByText('service').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Alpha agent')).toBeTruthy();
    expect(screen.queryByText(/token|password|private_key/i)).toBeNull();
    expect(listGatewayAuditEvents).toHaveBeenCalledWith(100, expect.any(AbortSignal));
    expect(listAgentAuditEvents).toHaveBeenCalledWith('agent-a', 100, expect.any(AbortSignal));
  });

  it('does not request agent audit when no active agent is selected', async () => {
    activeAgent.id = null;

    render(<AuditStatusPage />);

    expect(await screen.findByText('No active agent selected.')).toBeTruthy();
    await waitFor(() => expect(listGatewayAuditEvents).toHaveBeenCalledWith(100, expect.any(AbortSignal)));
    expect(listAgentAuditEvents).not.toHaveBeenCalled();
  });

  it('uses only the abortable local Agent audit endpoint in standalone mode', async () => {
    activeAgent.id = 'local';
    render(<AuditStatusPage mode="standalone" />);

    expect(await screen.findByRole('heading', { level: 1, name: 'Audit Status' })).toBeTruthy();
    expect(screen.queryByText('Gateway audit')).toBeNull();
    expect(listGatewayAuditEvents).not.toHaveBeenCalled();
    await waitFor(() => expect(listAgentAuditEvents).toHaveBeenCalledWith('local', 100, expect.any(AbortSignal)));
  });
});
