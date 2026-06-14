import { useEffect } from 'react';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TerminalPage } from '../src/pages/TerminalPage';

const activeAgent = vi.hoisted(() => ({ id: 'agent-a' as string | null }));
const paneMounts = vi.hoisted(() => vi.fn());
const paneUnmounts = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

function terminal(title: string, outputCursor: number) {
  return {
    id: 'term-shared',
    owner: 'local-admin',
    title,
    pid: 123,
    rows: 24,
    cols: 80,
    status: 'running',
    output_cursor: outputCursor,
    replay_buffer_start_cursor: 0,
    idle_timeout_minutes: 60,
    created_at: new Date().toISOString(),
    last_active_at: new Date().toISOString(),
    exited_at: null,
    closed_at: null,
    close_reason: null,
  };
}

const listTerminals = vi.hoisted(() => vi.fn());
const createTerminal = vi.hoisted(() => vi.fn());
const closeTerminal = vi.hoisted(() => vi.fn());

vi.mock('../src/agents/AgentContext', () => ({
  useActiveAgent: () => ({ activeAgentId: activeAgent.id, activeAgent: activeAgent.id ? { id: activeAgent.id, enabled: true, status: 'ready', capabilities: CAPABILITIES } : null }),
  agentSupports: (agent: { capabilities?: string[] } | null, capability: string) => Boolean(agent?.capabilities?.includes(capability)),
}));

vi.mock('../src/api/terminals', () => ({
  listTerminals,
  createTerminal,
  closeTerminal,
}));

vi.mock('../src/terminal/TerminalPane', () => ({
  TerminalPane: ({ agentId, terminalId }: { agentId: string; terminalId: string }) => {
    useEffect(() => {
      const key = `${agentId}:${terminalId}`;
      paneMounts(key);
      return () => paneUnmounts(key);
    }, []);

    return <div aria-label="Terminal">pane {agentId}:{terminalId}</div>;
  },
}));

describe('TerminalPage agent routing', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    activeAgent.id = 'agent-a';
    paneMounts.mockClear();
    paneUnmounts.mockClear();
    listTerminals.mockReset();
    createTerminal.mockReset();
    closeTerminal.mockReset();
  });

  it('loads active-agent terminals and remounts panes when the agent changes', async () => {
    listTerminals.mockImplementation(async (agentId: string) => [
      terminal(agentId === 'agent-a' ? 'Alpha terminal' : 'Beta terminal', agentId === 'agent-a' ? 10 : 20),
    ]);

    const { rerender } = render(<TerminalPage />);

    expect(await screen.findByText('Alpha terminal')).toBeTruthy();
    expect(screen.getByText('pane agent-a:term-shared')).toBeTruthy();
    await waitFor(() => expect(paneMounts).toHaveBeenCalledWith('agent-a:term-shared'));

    activeAgent.id = 'agent-b';
    rerender(<TerminalPage />);

    expect(await screen.findByText('Beta terminal')).toBeTruthy();
    expect(screen.queryByText('Alpha terminal')).toBeNull();
    expect(screen.getByText('pane agent-b:term-shared')).toBeTruthy();
    await waitFor(() => expect(paneUnmounts).toHaveBeenCalledWith('agent-a:term-shared'));
    expect(paneMounts).toHaveBeenCalledWith('agent-b:term-shared');
    expect(listTerminals).toHaveBeenCalledWith('agent-a', expect.anything());
    expect(listTerminals).toHaveBeenCalledWith('agent-b', expect.anything());
  });

  it('keeps stale terminal responses from previous agents out of the current state', async () => {
    let resolveAlpha: (value: ReturnType<typeof terminal>[]) => void = () => {};
    listTerminals.mockImplementation((agentId: string) => {
      if (agentId === 'agent-a') {
        return new Promise((resolve) => {
          resolveAlpha = resolve;
        });
      }

      return Promise.resolve([terminal('Beta terminal', 20)]);
    });

    const { rerender } = render(<TerminalPage />);

    activeAgent.id = 'agent-b';
    rerender(<TerminalPage />);

    expect(await screen.findByText('Beta terminal')).toBeTruthy();

    await act(async () => {
      resolveAlpha([terminal('Alpha stale terminal', 10)]);
      await Promise.resolve();
    });

    expect(screen.queryByText('Alpha stale terminal')).toBeNull();
    expect(screen.getByText('Beta terminal')).toBeTruthy();
  });
});
