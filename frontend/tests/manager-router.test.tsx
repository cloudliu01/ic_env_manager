import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock('../src/shared/api/client', () => ({
  apiClient: { request: apiRequest, setToken: vi.fn(), setUnauthorizedHandler: vi.fn() },
}));

function agent(id: string, capabilities = ['observations.v2']) {
  return {
    agent_id: id,
    display_name: id === 'agent-a' ? 'Alpha' : 'Beta',
    enabled: true,
    connection_status: 'ready',
    workload_status: 'healthy',
    capabilities,
  };
}

function renderManagerAt(path: string) {
  window.history.replaceState({}, '', path);
  return render(<App />);
}

describe('manager router', () => {
  beforeEach(() => {
    window.sessionStorage.setItem('ic-env-guard-token', 'manager-test-token');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/agent-b') return { agent: agent('agent-b') };
      if (path === '/api/v2/agents/agent-b/observations') return { items: [] };
      return { agents: [] };
    });
  });

  afterEach(() => cleanup());

  it('loads an agent deep link from the route id', async () => {
    renderManagerAt('/agents/agent-b/observations');

    expect(await screen.findByRole('heading', { name: 'Observations' })).toBeTruthy();
    expect(apiRequest).toHaveBeenCalledWith('/api/v2/agents/agent-b/observations', expect.anything());
  });

  it('shows an unavailable capability with an explanation and return link', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/agent-b') return { agent: agent('agent-b', []) };
      return { items: [] };
    });

    renderManagerAt('/agents/agent-b/observations');

    expect(await screen.findByRole('heading', { name: 'Feature unavailable' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('observations.v2');
    expect(screen.getByRole('link', { name: 'Return to agent overview' }).getAttribute('href')).toBe('/agents/agent-b/overview');
  });

  it('does not request observations for an unsupported deep link', async () => {
    apiRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agents/agent-b') return { agent: agent('agent-b', []) };
      throw new Error(`Unexpected request: ${path}`);
    });

    renderManagerAt('/agents/agent-b/observations');

    expect(await screen.findByRole('heading', { name: 'Feature unavailable' })).toBeTruthy();
    expect(apiRequest).not.toHaveBeenCalledWith('/api/v2/agents/agent-b/observations', expect.anything());
  });

  it('keeps observation filters in the URL across history navigation', async () => {
    renderManagerAt('/agents/agent-b/observations?status=critical');
    await screen.findByRole('heading', { name: 'Observations' });

    expect((screen.getByLabelText('Observation status filter') as HTMLSelectElement).value).toBe('critical');
    window.history.pushState({}, '', '/agents/agent-b/observations?status=warning');
    window.dispatchEvent(new PopStateEvent('popstate'));

    await waitFor(() => expect((screen.getByLabelText('Observation status filter') as HTMLSelectElement).value).toBe('warning'));
  });

  it('keeps filter focus instead of moving it to the route heading', async () => {
    const user = userEvent.setup();
    renderManagerAt('/agents/agent-b/observations');
    const filter = await screen.findByLabelText('Observation status filter');

    await user.selectOptions(filter, 'critical');

    await waitFor(() => expect(window.location.search).toBe('?status=critical'));
    expect(document.activeElement).toBe(filter);
  });
});
