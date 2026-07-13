import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock('../src/shared/api/client', () => ({
  apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() },
}));

function agent(agentId: string) {
  return {
    agent_id: agentId,
    display_name: agentId,
    enabled: true,
    connection_status: 'ready',
    workload_status: 'healthy',
    capabilities: ['observations.v2'],
  };
}

describe('agent query isolation', () => {
  beforeEach(() => {
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    apiRequest.mockReset();
    window.history.replaceState({}, '', '/agents/agent-a/observations');
  });

  afterEach(() => cleanup());

  it('cannot let a slow prior agent response overwrite the route target', async () => {
    let resolveAgentA!: (value: { items: Array<{ name: string }> }) => void;
    apiRequest.mockImplementation((path: string) => {
      if (path === '/api/v2/runtime') return Promise.resolve({ mode: 'manager', capabilities: ['agent-registry.v2'] });
      if (path === '/api/v2/agents/agent-a') return Promise.resolve({ agent: agent('agent-a') });
      if (path === '/api/v2/agents/agent-b') return Promise.resolve({ agent: agent('agent-b') });
      if (path === '/api/v2/agents/agent-a/observations') {
        return new Promise((resolve) => { resolveAgentA = resolve; });
      }
      if (path === '/api/v2/agents/agent-b/observations') return Promise.resolve({ items: [{ name: 'Beta result' }] });
      return Promise.resolve({ items: [] });
    });

    render(<App />);
    await screen.findByRole('heading', { name: 'Observations' });

    window.history.pushState({}, '', '/agents/agent-b/observations');
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(await screen.findByText('Beta result')).toBeTruthy();
    resolveAgentA({ items: [{ name: 'Alpha result' }] });
    await Promise.resolve();

    expect(screen.getByText('Beta result')).toBeTruthy();
    expect(screen.queryByText('Alpha result')).toBeNull();
  });

});
