import { useEffect, useRef, useState } from 'react';
import { closeTerminal, createTerminal, listTerminals } from './api';
import { TerminalPane } from './TerminalPane';
import { AgentTarget, TerminalSession } from './types';

type TerminalPageProps = {
  target: AgentTarget;
  visible?: boolean;
};

type TerminalAgentState = {
  terminals: TerminalSession[];
  activeId: string | null;
  error: string | null;
};

const emptyAgentState: TerminalAgentState = { terminals: [], activeId: null, error: null };

export function TerminalPage({ target, visible = true }: TerminalPageProps) {
  const { agentId, capabilities } = target;
  const supportsTerminals = capabilities.includes('terminals.v1');
  const [terminalStateByAgent, setTerminalStateByAgent] = useState<Record<string, TerminalAgentState>>({});
  const requestGeneration = useRef(0);
  const activeState = terminalStateByAgent[agentId] ?? emptyAgentState;
  const { terminals, activeId, error } = activeState;

  function updateAgentState(agentId: string, updater: (current: TerminalAgentState) => TerminalAgentState) {
    setTerminalStateByAgent((current) => ({
      ...current,
      [agentId]: updater(current[agentId] ?? emptyAgentState),
    }));
  }

  async function refresh(signal?: AbortSignal) {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    if (!supportsTerminals) {
      return;
    }

    try {
      const items = await listTerminals(agentId, signal ? { signal } : undefined);
      if (requestGeneration.current !== generation) {
        return;
      }
      updateAgentState(agentId, (current) => {
        const nextActiveId = current.activeId && items.some((terminal) => terminal.id === current.activeId)
          ? current.activeId
          : items.find((terminal) => terminal.status === 'running')?.id ?? items[0]?.id ?? null;
        return { terminals: items, activeId: nextActiveId, error: null };
      });
    } catch (err) {
      if ((err as Error).name !== 'AbortError' && requestGeneration.current === generation) {
        updateAgentState(agentId, (current) => ({ ...current, error: (err as Error).message }));
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
    // refresh intentionally reads the current agent target for this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, supportsTerminals]);

  async function openTerminal() {
    if (!supportsTerminals) {
      return;
    }
    updateAgentState(agentId, (current) => ({ ...current, error: null }));
    try {
      const terminal = await createTerminal(agentId, `Terminal ${terminals.length + 1}`);
      updateAgentState(agentId, (current) => ({
        terminals: [...current.terminals, terminal],
        activeId: terminal.id,
        error: null,
      }));
    } catch (err) {
      updateAgentState(agentId, (current) => ({ ...current, error: (err as Error).message }));
    }
  }

  async function closeActiveTerminal() {
    if (!activeId || !supportsTerminals) {
      return;
    }
    updateAgentState(agentId, (current) => ({ ...current, error: null }));
    try {
      const closed = await closeTerminal(agentId, activeId);
      updateAgentState(agentId, (current) => ({
        ...current,
        terminals: current.terminals.map((item) => (item.id === closed.id ? closed : item)),
      }));
    } catch (err) {
      updateAgentState(agentId, (current) => ({ ...current, error: (err as Error).message }));
    }
  }

  function selectTerminal(id: string) {
    updateAgentState(agentId, (current) => ({ ...current, activeId: id }));
  }

  const active = terminals.find((terminal) => terminal.id === activeId) ?? null;

  return (
    <section className="terminal-page">
      <header className="terminal-page-header">
        <div>
          <h2>Terminals</h2>
          <p>Interactive shell sessions are attached through single-use websocket tickets.</p>
        </div>
        <div className="terminal-actions">
          <button type="button" onClick={openTerminal} disabled={!supportsTerminals}>New terminal</button>
          <button type="button" onClick={closeActiveTerminal} disabled={!supportsTerminals || !active || active.status !== 'running'}>
            Close terminal
          </button>
          <button type="button" onClick={() => void refresh()} disabled={!supportsTerminals}>Refresh</button>
        </div>
      </header>
      {error ? <p role="alert" className="terminal-error">{error}</p> : null}
      <div className="terminal-layout">
        <div role="tablist" aria-label="Terminal sessions" className="terminal-tabs">
          {terminals.map((terminal) => (
            <button
              key={terminal.id}
              type="button"
              role="tab"
              aria-selected={terminal.id === activeId}
              className={terminal.id === activeId ? 'terminal-tab terminal-tab-active' : 'terminal-tab'}
              onClick={() => selectTerminal(terminal.id)}
            >
              <span>{terminal.title}</span>
              <span className={`terminal-status terminal-status-${terminal.status}`}>{terminal.status}</span>
            </button>
          ))}
        </div>
        {active ? (
          <TerminalPane
            key={`${agentId}:${active.id}`}
            agentId={agentId}
            terminalId={active.id}
            initialCursor={active.output_cursor}
            status={active.status}
            active={visible}
          />
        ) : (
          <div className="terminal-empty">
            <p>{supportsTerminals ? 'No terminal sessions.' : 'This Agent does not support terminals.'}</p>
            {supportsTerminals ? <button type="button" onClick={openTerminal}>Open a terminal</button> : null}
          </div>
        )}
      </div>
    </section>
  );
}
