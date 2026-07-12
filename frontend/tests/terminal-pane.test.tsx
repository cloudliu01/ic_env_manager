import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TerminalPane } from '../src/features/terminal/TerminalPane';
import { apiClient } from '../src/shared/api/client';
import { createConnectToken, resizeTerminal } from '../src/features/terminal/api';

const terminalWrites: string[] = [];
const terminalOpenElements: Element[] = [];
const terminalInputs: Array<(data: string) => void> = [];
const terminalResizes: Array<(size: { rows: number; cols: number }) => void> = [];
const terminalDisposables: Array<{ dispose: () => void }> = [];

type Listener = (event?: { data: string | ArrayBuffer }) => void;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;

  readyState = MockWebSocket.OPEN;
  sent: string[] = [];
  closed = false;
  listeners: Record<string, Listener[]> = {};

  constructor(
    public readonly url: string,
    public readonly protocols: string[] = [],
  ) {
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
    this.readyState = 3;
  }

  emit(type: string, data = '') {
    for (const listener of this.listeners[type] ?? []) {
      listener({ data });
    }
  }
}

vi.mock('../src/features/terminal/api', () => ({
  createConnectToken: vi.fn(async () => ({ ticket: 'ticket-1', expires_in_seconds: 60 })),
  resizeTerminal: vi.fn(async () => undefined),
}));

vi.mock('xterm', () => {
  class MockTerminal {
    rows = 24;
    cols = 80;
    loadAddon = vi.fn();
    open = vi.fn((element: Element) => terminalOpenElements.push(element));
    focus = vi.fn();
    write = vi.fn((data: string) => terminalWrites.push(data));
    writeln = vi.fn((data: string) => terminalWrites.push(`${data}\n`));
    dispose = vi.fn();

    onData(callback: (data: string) => void) {
      terminalInputs.push(callback);
      const disposable = { dispose: vi.fn() };
      terminalDisposables.push(disposable);
      return disposable;
    }

    onResize(callback: (size: { rows: number; cols: number }) => void) {
      terminalResizes.push(callback);
      const disposable = { dispose: vi.fn() };
      terminalDisposables.push(disposable);
      return disposable;
    }
  }

  return { Terminal: MockTerminal };
});

vi.mock('@xterm/addon-fit', () => {
  class MockFitAddon {
    fit = vi.fn();
  }

  return { FitAddon: MockFitAddon };
});

beforeEach(() => {
  terminalWrites.length = 0;
  terminalOpenElements.length = 0;
  terminalInputs.length = 0;
  terminalResizes.length = 0;
  terminalDisposables.length = 0;
  MockWebSocket.instances.length = 0;
  vi.clearAllMocks();
  apiClient.setToken('secret-token');

  class MockResizeObserver {
    observe = vi.fn();
    disconnect = vi.fn();
  }

  Object.defineProperty(window, 'ResizeObserver', {
    configurable: true,
    writable: true,
    value: MockResizeObserver,
  });
  Object.defineProperty(window, 'WebSocket', {
    configurable: true,
    writable: true,
    value: MockWebSocket,
  });
  Object.defineProperty(MockWebSocket, 'OPEN', { value: 1 });
  Element.prototype.getBoundingClientRect = vi.fn(() => ({
    x: 0,
    y: 0,
    width: 800,
    height: 400,
    top: 0,
    right: 800,
    bottom: 400,
    left: 0,
    toJSON: () => ({}),
  }));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('TerminalPane', () => {
  it('opens xterm in an unpadded full-size mount element', async () => {
    render(<TerminalPane agentId="agent-a" terminalId="term-1" status="running" />);

    await waitFor(() => expect(terminalOpenElements).toHaveLength(1));
    expect(terminalOpenElements[0].classList.contains('terminal-mount')).toBe(true);
    expect(terminalOpenElements[0].parentElement?.classList.contains('terminal-pane')).toBe(true);
  });

  it('writes websocket output exactly once and sends user input', async () => {
    render(<TerminalPane agentId="agent-a" terminalId="term-1" status="running" />);

    await waitFor(() => expect(createConnectToken).toHaveBeenCalledWith('agent-a', 'term-1'));
    const socket = MockWebSocket.instances[0];
  expect(socket.url).toContain('/ws/agents/agent-a/terminals/term-1?');
  expect(socket.url).toContain('ticket=ticket-1');
  expect(socket.protocols).toEqual(['bearer.c2VjcmV0LXRva2Vu']);

    act(() => {
      socket.emit('open');
      socket.emit('message', 'shell output');
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(terminalWrites).toEqual(['shell output']);

    act(() => {
      terminalInputs[0]('ls\r');
    });

    expect(socket.sent).toEqual(['ls\r']);
    expect(screen.getByText('connected')).toBeTruthy();
  });

  it('ignores binary websocket control frames instead of writing them as text', async () => {
    render(<TerminalPane agentId="agent-a" terminalId="term-1" status="running" />);

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];

    act(() => {
      for (const listener of socket.listeners.message ?? []) {
        listener({ data: new ArrayBuffer(8) });
      }
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(terminalWrites).toEqual([]);
  });

  it('ignores stale websocket messages after unmount', async () => {
    const { unmount } = render(<TerminalPane agentId="agent-a" terminalId="term-1" status="running" />);

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const socket = MockWebSocket.instances[0];

    unmount();

    act(() => {
      socket.emit('message', 'stale output');
    });

    expect(socket.closed).toBe(true);
    expect(terminalWrites).toEqual([]);
  });

  it('closes the old Agent socket and ignores its output when the Agent target changes', async () => {
    const { rerender } = render(<TerminalPane agentId="agent-a" terminalId="term-1" status="running" />);
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1));
    const oldSocket = MockWebSocket.instances[0];

    rerender(<TerminalPane agentId="agent-b" terminalId="term-1" status="running" />);
    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));
    const newSocket = MockWebSocket.instances[1];

    expect(oldSocket.closed).toBe(true);
    act(() => oldSocket.emit('message', 'alpha output'));
    act(() => newSocket.emit('message', 'beta output'));
    await act(async () => { await Promise.resolve(); });
    expect(terminalWrites).toEqual(['beta output']);
  });

  it('deduplicates terminal resize updates', async () => {
    vi.useFakeTimers();
    render(<TerminalPane agentId="agent-a" terminalId="term-1" status="running" />);

    act(() => {
      terminalResizes[0]({ rows: 24, cols: 80 });
      terminalResizes[0]({ rows: 24, cols: 80 });
      vi.runAllTimers();
    });

    expect(resizeTerminal).toHaveBeenCalledTimes(1);
    expect(resizeTerminal).toHaveBeenCalledWith('agent-a', 'term-1', 24, 80);
    vi.useRealTimers();
  });
});
