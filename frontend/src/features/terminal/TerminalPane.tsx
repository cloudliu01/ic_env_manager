import { useEffect, useRef, useState } from 'react';
import { Terminal } from 'xterm';
import 'xterm/css/xterm.css';
import { FitAddon } from '@xterm/addon-fit';
import { createConnectToken, resizeTerminal } from './api';
import { apiClient } from '../../shared/api/client';
import { terminalWriter } from '../../terminal/terminalWriter';

export type TerminalPaneProps = {
  agentId: string;
  terminalId: string;
  initialCursor?: number;
  status?: string;
  active?: boolean;
};

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'closed' | 'error';

function terminalWebSocketUrl(agentId: string, terminalId: string, ticket: string, cursor: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({ ticket, cursor: String(cursor) });
  if (agentId === 'local') {
    return `${protocol}//${window.location.host}/ws/terminals/${encodeURIComponent(terminalId)}?${params.toString()}`;
  }
  return `${protocol}//${window.location.host}/ws/agents/${encodeURIComponent(agentId)}/terminals/${encodeURIComponent(terminalId)}?${params.toString()}`;
}

export function TerminalPane({
  agentId,
  terminalId,
  initialCursor = 0,
  status = 'running',
  active = true,
}: TerminalPaneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const cursorRef = useRef(initialCursor);
  const encoderRef = useRef(new TextEncoder());
  const resizeDebounceRef = useRef<number | null>(null);
  const lastResizeRef = useRef<{ rows: number; cols: number } | null>(null);
  const fitFrameRef = useRef<number | null>(null);
  const connectionGenerationRef = useRef(0);
  const writerRef = useRef<ReturnType<typeof terminalWriter> | null>(null);
  const wasActiveRef = useRef(active);
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting');

  useEffect(() => {
    cursorRef.current = initialCursor;
  }, [initialCursor, agentId, terminalId]);

  useEffect(() => {
    if (!containerRef.current || terminalRef.current) {
      return;
    }

    const terminal = new Terminal({
      cursorBlink: true,
      cursorStyle: 'bar',
      convertEol: false,
      fontFamily: 'Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      scrollback: 10000,
      theme: {
        background: '#0b1020',
        foreground: '#e5e7eb',
        cursor: '#f8fafc',
        selectionBackground: '#334155',
        black: '#0f172a',
        red: '#f87171',
        green: '#34d399',
        yellow: '#fbbf24',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#e5e7eb',
        brightBlack: '#64748b',
        brightRed: '#fca5a5',
        brightGreen: '#86efac',
        brightYellow: '#fde68a',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#67e8f9',
        brightWhite: '#ffffff',
      },
    });
    const fit = new FitAddon();
    const writer = terminalWriter((data, done) => {
      terminal.write(data, () => {
        done?.();
      });
    });
    terminal.loadAddon(fit);
    terminal.open(containerRef.current);
    terminal.focus();

    const fitTerminal = () => {
      if (!containerRef.current) {
        return;
      }
      const { width, height } = containerRef.current.getBoundingClientRect();
      if (width <= 0 || height <= 0) {
        return;
      }
      fit.fit();
      lastResizeRef.current = { rows: terminal.rows, cols: terminal.cols };
      void resizeTerminal(agentId, terminalId, terminal.rows, terminal.cols);
    };

    fitTerminal();

    const resizeObserver = new ResizeObserver(() => {
      if (fitFrameRef.current !== null) {
        window.cancelAnimationFrame(fitFrameRef.current);
      }
      fitFrameRef.current = window.requestAnimationFrame(() => {
        fitFrameRef.current = null;
        fitTerminal();
      });
    });
    resizeObserver.observe(containerRef.current);

    terminalRef.current = terminal;
    fitRef.current = fit;
    writerRef.current = writer;

    return () => {
      resizeObserver.disconnect();
      if (fitFrameRef.current !== null) {
        window.cancelAnimationFrame(fitFrameRef.current);
        fitFrameRef.current = null;
      }
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
      writerRef.current = null;
    };
  }, [agentId, terminalId]);

  useEffect(() => {
    const becameActive = active && !wasActiveRef.current;
    wasActiveRef.current = active;
    if (!becameActive) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const terminal = terminalRef.current;
      const fit = fitRef.current;
      const container = containerRef.current;
      if (!terminal || !fit || !container) {
        return;
      }
      const { width, height } = container.getBoundingClientRect();
      if (width <= 0 || height <= 0) {
        return;
      }
      fit.fit();
      terminal.focus();
      lastResizeRef.current = { rows: terminal.rows, cols: terminal.cols };
      void resizeTerminal(agentId, terminalId, terminal.rows, terminal.cols);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, agentId, terminalId]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) {
      return;
    }

    const disposable = terminal.onResize((size) => {
      const lastResize = lastResizeRef.current;
      if (lastResize?.rows === size.rows && lastResize.cols === size.cols) {
        return;
      }
      lastResizeRef.current = size;
      if (resizeDebounceRef.current !== null) {
        window.clearTimeout(resizeDebounceRef.current);
      }
      resizeDebounceRef.current = window.setTimeout(() => {
        void resizeTerminal(agentId, terminalId, size.rows, size.cols);
      }, 100);
    });

    return () => {
      disposable.dispose();
      if (resizeDebounceRef.current !== null) {
        window.clearTimeout(resizeDebounceRef.current);
        resizeDebounceRef.current = null;
      }
    };
  }, [agentId, terminalId]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) {
      return;
    }
    const activeTerminal = terminal;

    if (status !== 'running') {
      setConnectionState('closed');
      activeTerminal.writeln(`\r\n[terminal ${status}]`);
      return;
    }

    let cancelled = false;
    let socket: WebSocket | null = null;
    let inputDisposable: { dispose: () => void } | null = null;
    const generation = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = generation;
    const isCurrentConnection = () => !cancelled && connectionGenerationRef.current === generation;

    async function connect() {
      setConnectionState('connecting');
      try {
        const { ticket } = await createConnectToken(agentId, terminalId);
        if (!isCurrentConnection()) {
          return;
        }

        socket = new WebSocket(
          terminalWebSocketUrl(agentId, terminalId, ticket, cursorRef.current),
          apiClient.webSocketProtocols(),
        );
        socket.binaryType = 'arraybuffer';
        if (!isCurrentConnection()) {
          socket.close();
          return;
        }
        socketRef.current = socket;

        inputDisposable = activeTerminal.onData((data) => {
          if (isCurrentConnection() && socket?.readyState === WebSocket.OPEN) {
            socket.send(data);
          }
        });

        socket.addEventListener('open', () => {
          if (!isCurrentConnection() || socket !== socketRef.current) {
            return;
          }
          setConnectionState('connected');
          activeTerminal.focus();
        });

        socket.addEventListener('message', (event) => {
          if (!isCurrentConnection() || socket !== socketRef.current) {
            return;
          }
          if (typeof event.data !== 'string' || !event.data) {
            return;
          }
          const data = event.data;
          cursorRef.current += encoderRef.current.encode(data).length;
          writerRef.current?.push(data);
        });

        socket.addEventListener('close', () => {
          if (isCurrentConnection() && socket === socketRef.current) {
            setConnectionState('disconnected');
          }
        });

        socket.addEventListener('error', () => {
          if (isCurrentConnection() && socket === socketRef.current) {
            setConnectionState('error');
          }
        });
      } catch (error) {
        if (!cancelled) {
          setConnectionState('error');
          activeTerminal.writeln(`\r\n[terminal connection failed: ${(error as Error).message}]`);
        }
      }
    }

    void connect();

    return () => {
      cancelled = true;
      inputDisposable?.dispose();
      socket?.close();
      writerRef.current?.flush();
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    };
  }, [agentId, terminalId, status]);

  return (
    <div className="terminal-shell">
      <div className="terminal-toolbar">
        <span className={`terminal-connection terminal-connection-${connectionState}`}>
          {connectionState}
        </span>
      </div>
      <div className="terminal-pane">
        <div className="terminal-mount" ref={containerRef} aria-label="Terminal" />
      </div>
    </div>
  );
}
