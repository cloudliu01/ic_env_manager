import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ServiceListPage } from '../src/pages/ServiceListPage';

vi.mock('../src/agents/AgentContext', () => ({
  useActiveAgent: () => ({ activeAgentId: 'agent-a', activeAgent: { id: 'agent-a', enabled: true, status: 'ready', capabilities: ['services.v1'] } }),
  agentSupports: (agent: { capabilities?: string[] } | null, capability: string) => Boolean(agent?.capabilities?.includes(capability)),
}));

vi.mock('../src/api/services', () => ({
  listServices: vi.fn(async () => [{ id: 'demo', name: 'Demo', status: 'configured', health_status: 'unknown', allowed_operations: ['start', 'stop'] }]),
  startService: vi.fn(async () => ({})),
  stopService: vi.fn(async () => ({})),
}));

describe('ServiceListPage', () => {
  it('renders configured services', async () => {
    render(<ServiceListPage />);
    expect(await screen.findByText('Demo')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start' })).toBeTruthy();
  });
});
