import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuditStatusPage } from '../src/pages/AuditStatusPage';

const listAgentAuditEvents = vi.hoisted(() => vi.fn());

vi.mock('../src/agents/StandaloneAgentContext', () => ({
  useStandaloneAgent: () => ({ agentId: 'local', name: 'Build node', capabilities: ['audit.v1'] }),
  supportsCapability: (capabilities: string[], capability: string) => capabilities.includes(capability),
}));

vi.mock('../src/api/audit', () => ({ listAgentAuditEvents }));

describe('AuditStatusPage', () => {
  beforeEach(() => {
    listAgentAuditEvents.mockResolvedValue({ events: [{ id: 2, timestamp: '2026-06-13T00:00:01Z', agent_id: 'local', operation: 'service.start', target_type: 'service', result: 'success' }] });
  });

  afterEach(() => cleanup());

  it('loads audit records for the standalone Agent only', async () => {
    render(<AuditStatusPage />);

    expect(await screen.findByText('service.start')).toBeTruthy();
    expect(screen.queryByText('Gateway audit')).toBeNull();
    expect(listAgentAuditEvents).toHaveBeenCalledWith('local', 100, expect.any(AbortSignal));
  });
});
