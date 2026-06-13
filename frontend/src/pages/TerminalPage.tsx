import { useEffect, useState } from 'react';
import { closeTerminal, createTerminal, listTerminals, TerminalSession } from '../api/terminals';
import { TerminalPane } from '../terminal/TerminalPane';

type TerminalPageProps = {
  visible?: boolean;
};

export function TerminalPage({ visible = true }: TerminalPageProps) {
  const [terminals, setTerminals] = useState<TerminalSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const items = await listTerminals();
      setTerminals(items);
      setActiveId((current) => {
        if (current && items.some((terminal) => terminal.id === current)) {
          return current;
        }
        return items.find((terminal) => terminal.status === 'running')?.id ?? items[0]?.id ?? null;
      });
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function openTerminal() {
    setError(null);
    try {
      const terminal = await createTerminal(`Terminal ${terminals.length + 1}`);
      setTerminals((current) => [...current, terminal]);
      setActiveId(terminal.id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function closeActiveTerminal() {
    if (!activeId) {
      return;
    }
    setError(null);
    try {
      const closed = await closeTerminal(activeId);
      setTerminals((current) => current.map((item) => (item.id === closed.id ? closed : item)));
    } catch (err) {
      setError((err as Error).message);
    }
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
          <button type="button" onClick={openTerminal}>New terminal</button>
          <button type="button" onClick={closeActiveTerminal} disabled={!active || active.status !== 'running'}>
            Close terminal
          </button>
          <button type="button" onClick={() => void refresh()}>Refresh</button>
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
              onClick={() => setActiveId(terminal.id)}
            >
              <span>{terminal.title}</span>
              <span className={`terminal-status terminal-status-${terminal.status}`}>{terminal.status}</span>
            </button>
          ))}
        </div>
        {active ? (
          <TerminalPane
            key={active.id}
            terminalId={active.id}
            initialCursor={active.output_cursor}
            status={active.status}
            active={visible}
          />
        ) : (
          <div className="terminal-empty">
            <p>No terminal sessions.</p>
            <button type="button" onClick={openTerminal}>Open a terminal</button>
          </div>
        )}
      </div>
    </section>
  );
}
