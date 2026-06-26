import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentProvider, useActiveAgent } from '../src/agents/AgentContext';
import { AgentSelector } from '../src/agents/AgentSelector';

const listAgents = vi.hoisted(() => vi.fn());
const getFleetOverview = vi.hoisted(() => vi.fn());
const setAgentEnabled = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/api/agents', () => ({
  listAgents,
}));

vi.mock('../src/api/fleet', () => ({
  getFleetOverview,
  setAgentEnabled,
}));

function fleet(hosts: Array<Record<string, unknown>>) {
  return { collected_at: '2026-06-26T00:00:00Z', hosts };
}

function Probe() {
  const { activeAgent, activeAgentId, agents, fleetHosts, setHostEnabled } = useActiveAgent();

  return (
    <>
      <AgentSelector />
      <p>Active: {activeAgentId ?? 'none'}</p>
      <p>Name: {activeAgent?.name ?? 'none'}</p>
      <p>Agent count: {agents.length}</p>
      <p>Fleet count: {fleetHosts.length}</p>
      <button type="button" onClick={() => void setHostEnabled('agent-a', false)}>Disable Alpha</button>
    </>
  );
}

describe('AgentProvider', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    window.sessionStorage.clear();
    listAgents.mockReset();
    getFleetOverview.mockReset();
    setAgentEnabled.mockReset();
  });

  it('selects a stored agent id when that agent is still present', async () => {
    window.sessionStorage.setItem('activeAgentId', 'agent-b');
    getFleetOverview.mockResolvedValue(fleet([
      { id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES },
      { id: 'agent-b', name: 'Beta', status: 'enabled', enabled: true, capabilities: [] },
    ]));

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-b')).toBeTruthy();
    expect(screen.getByText('Name: Beta')).toBeTruthy();
    expect(screen.getByText('Fleet count: 2')).toBeTruthy();
    expect(window.sessionStorage.getItem('activeAgentId')).toBe('agent-b');
  });

  it('falls back to the first ready agent, then persists only the selected id', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('activeAgentId', 'missing-agent');
    getFleetOverview.mockResolvedValue(fleet([
      { id: 'agent-a', name: 'Alpha', status: 'enabled', enabled: true, capabilities: [], base_url: 'https://alpha.invalid' },
      { id: 'agent-b', name: 'Beta', status: 'ready', enabled: true, capabilities: CAPABILITIES, token_path: '/secret/token' },
    ]));

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-b')).toBeTruthy();

    await user.selectOptions(screen.getByLabelText('Active agent'), 'agent-a');

    await waitFor(() => expect(window.sessionStorage.getItem('activeAgentId')).toBe('agent-a'));
    expect(window.sessionStorage.getItem('base_url')).toBeNull();
    expect(window.sessionStorage.getItem('token_path')).toBeNull();
    expect(window.sessionStorage.length).toBe(1);
  });

  it('uses the first enabled agent when none are ready and reports no active agent when none are enabled', async () => {
    getFleetOverview.mockResolvedValueOnce(fleet([
      { id: 'disabled', name: 'Disabled', status: 'disabled', enabled: false, capabilities: [] },
      { id: 'agent-a', name: 'Alpha', status: 'offline', enabled: true, capabilities: [] },
    ]));

    const { unmount } = render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-a')).toBeTruthy();
    unmount();
    window.sessionStorage.clear();

    getFleetOverview.mockResolvedValueOnce(fleet([
      { id: 'disabled', name: 'Disabled', status: 'disabled', enabled: false, capabilities: [] },
    ]));

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: none')).toBeTruthy();
  });

  it('falls back to legacy agent inventory when fleet overview is unavailable', async () => {
    getFleetOverview.mockRejectedValue(new Error('missing fleet endpoint'));
    listAgents.mockResolvedValue([
      { id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES },
    ]);

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-a')).toBeTruthy();
    expect(screen.getByText('Agent count: 1')).toBeTruthy();
  });

  it('toggles host enabled state and refreshes fleet', async () => {
    const user = userEvent.setup();
    getFleetOverview
      .mockResolvedValueOnce(fleet([{ id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES }]))
      .mockResolvedValueOnce(fleet([{ id: 'agent-a', name: 'Alpha', status: 'disabled', enabled: false, capabilities: [] }]));
    setAgentEnabled.mockResolvedValue({ id: 'agent-a', name: 'Alpha', status: 'disabled', enabled: false, capabilities: [] });

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-a')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Disable Alpha' }));

    await waitFor(() => expect(setAgentEnabled).toHaveBeenCalledWith('agent-a', false));
    expect(await screen.findByText('Active: agent-a')).toBeTruthy();
    expect(screen.getByText('disabled')).toBeTruthy();
  });
});
