import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentProvider } from '../src/agents/AgentContext';
import { HostOverviewPage } from '../src/pages/HostOverviewPage';

const getFleetOverview = vi.hoisted(() => vi.fn());
const setAgentEnabled = vi.hoisted(() => vi.fn());
const listAgents = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/api/fleet', () => ({
  getFleetOverview,
  setAgentEnabled,
}));

vi.mock('../src/api/agents', () => ({
  listAgents,
}));

function overview() {
  return {
    collected_at: '2026-06-26T00:00:00Z',
    hosts: [
      { id: 'alpha', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES, observed_at: '2026-06-26T00:00:00Z', stale_after: '2026-06-26T00:00:30Z' },
      { id: 'beta', name: 'Beta', status: 'unavailable', enabled: true, capabilities: [], observed_at: '2026-06-25T23:59:00Z', stale_after: '2026-06-25T23:59:30Z', last_error: 'agent_timeout' },
      { id: 'gamma', name: 'Gamma', status: 'disabled', enabled: false, capabilities: [] },
    ],
  };
}

function renderPage(onOpenHost = vi.fn()) {
  return render(
    <AgentProvider>
      <HostOverviewPage onOpenHost={onOpenHost} />
    </AgentProvider>,
  );
}

describe('HostOverviewPage fleet overview', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    window.sessionStorage.clear();
    getFleetOverview.mockReset();
    setAgentEnabled.mockReset();
    listAgents.mockReset();
  });

  it('renders all hosts with status, capabilities, freshness, and safe errors', async () => {
    getFleetOverview.mockResolvedValue(overview());

    renderPage();

    expect(await screen.findByText('Fleet Overview')).toBeTruthy();
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.getByText('Gamma')).toBeTruthy();
    expect(screen.getByText('terminals.v1')).toBeTruthy();
    expect(screen.getByText('Last error: agent_timeout')).toBeTruthy();
    expect(screen.queryByText(/token/i)).toBeNull();
    expect(screen.queryByText(/base_url/i)).toBeNull();
  });

  it('supports search, status filter, and name sort', async () => {
    const user = userEvent.setup();
    getFleetOverview.mockResolvedValue(overview());

    renderPage();
    await screen.findByText('Alpha');

    await user.type(screen.getByLabelText('Search hosts'), 'beta');
    expect(screen.queryByText('Alpha')).toBeNull();
    expect(screen.getByText('Beta')).toBeTruthy();

    await user.clear(screen.getByLabelText('Search hosts'));
    await user.selectOptions(screen.getByLabelText('Status'), 'disabled');
    expect(screen.queryByText('Alpha')).toBeNull();
    expect(screen.getByText('Gamma')).toBeTruthy();

    await user.selectOptions(screen.getByLabelText('Status'), 'all');
    await user.selectOptions(screen.getByLabelText('Sort'), 'name');
    const cards = screen.getAllByRole('article');
    expect(within(cards[0]).getByText('Alpha')).toBeTruthy();
    expect(within(cards[1]).getByText('Beta')).toBeTruthy();
    expect(within(cards[2]).getByText('Gamma')).toBeTruthy();
  });

  it('selects and opens only ready enabled hosts', async () => {
    const user = userEvent.setup();
    const onOpenHost = vi.fn();
    getFleetOverview.mockResolvedValue(overview());

    renderPage(onOpenHost);
    await screen.findByText('Alpha');

    await user.click(within(screen.getByText('Alpha').closest('article')!).getByRole('button', { name: 'Manage' }));
    expect(onOpenHost).toHaveBeenCalledWith('alpha');

    expect(within(screen.getByText('Beta').closest('article')!).getByRole('button', { name: 'Manage' }).hasAttribute('disabled')).toBe(true);
    expect(within(screen.getByText('Gamma').closest('article')!).getByRole('button', { name: 'Manage' }).hasAttribute('disabled')).toBe(true);
  });

  it('toggles enable state and refreshes fleet with feedback', async () => {
    const user = userEvent.setup();
    getFleetOverview
      .mockResolvedValueOnce(overview())
      .mockResolvedValueOnce({
        ...overview(),
        hosts: overview().hosts.map((host) => host.id === 'alpha' ? { ...host, enabled: false, status: 'disabled', capabilities: [] } : host),
      });
    setAgentEnabled.mockResolvedValue({ id: 'alpha', name: 'Alpha', status: 'disabled', enabled: false, capabilities: [] });

    renderPage();
    await screen.findByText('Alpha');

    await user.click(within(screen.getByText('Alpha').closest('article')!).getByRole('button', { name: 'Disable' }));

    await waitFor(() => expect(setAgentEnabled).toHaveBeenCalledWith('alpha', false));
    expect(await screen.findByText('Alpha disabled.')).toBeTruthy();
  });
});
