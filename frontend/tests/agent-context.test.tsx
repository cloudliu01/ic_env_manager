import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentProvider, useActiveAgent } from '../src/agents/AgentContext';
import { AgentSelector } from '../src/agents/AgentSelector';

const listAgents = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/api/agents', () => ({
  listAgents,
}));

function Probe() {
  const { activeAgent, activeAgentId, agents } = useActiveAgent();

  return (
    <>
      <AgentSelector />
      <p>Active: {activeAgentId ?? 'none'}</p>
      <p>Name: {activeAgent?.name ?? 'none'}</p>
      <p>Agent count: {agents.length}</p>
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
  });

  it('selects a stored agent id when that agent is still present', async () => {
    window.sessionStorage.setItem('activeAgentId', 'agent-b');
    listAgents.mockResolvedValue([
      { id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES },
      { id: 'agent-b', name: 'Beta', status: 'enabled', enabled: true, capabilities: [] },
    ]);

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-b')).toBeTruthy();
    expect(screen.getByText('Name: Beta')).toBeTruthy();
    expect(window.sessionStorage.getItem('activeAgentId')).toBe('agent-b');
  });

  it('falls back to the first ready agent, then persists only the selected id', async () => {
    const user = userEvent.setup();
    window.sessionStorage.setItem('activeAgentId', 'missing-agent');
    listAgents.mockResolvedValue([
      { id: 'agent-a', name: 'Alpha', status: 'enabled', enabled: true, capabilities: [], base_url: 'https://alpha.invalid' },
      { id: 'agent-b', name: 'Beta', status: 'ready', enabled: true, capabilities: CAPABILITIES, token_path: '/secret/token' },
    ]);

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-b')).toBeTruthy();

    await user.selectOptions(screen.getByLabelText('Active agent'), 'agent-a');

    await waitFor(() => expect(window.sessionStorage.getItem('activeAgentId')).toBe('agent-a'));
    expect(window.sessionStorage.getItem('base_url')).toBeNull();
    expect(window.sessionStorage.getItem('token_path')).toBeNull();
    expect(window.sessionStorage.length).toBe(1);
  });

  it('uses the first enabled agent when none are ready and reports no active agent when none are enabled', async () => {
    listAgents.mockResolvedValueOnce([
      { id: 'disabled', name: 'Disabled', status: 'disabled', enabled: false, capabilities: [] },
      { id: 'agent-a', name: 'Alpha', status: 'offline', enabled: true, capabilities: [] },
    ]);

    const { unmount } = render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: agent-a')).toBeTruthy();
    unmount();
    window.sessionStorage.clear();

    listAgents.mockResolvedValueOnce([
      { id: 'disabled', name: 'Disabled', status: 'disabled', enabled: false, capabilities: [] },
    ]);

    render(<AgentProvider><Probe /></AgentProvider>);

    expect(await screen.findByText('Active: none')).toBeTruthy();
  });
});
