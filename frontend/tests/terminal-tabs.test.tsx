import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TerminalPage } from '../src/pages/TerminalPage';

vi.mock('../src/api/terminals', () => ({
  listTerminals: vi.fn(async () => []),
  createTerminal: vi.fn(async () => ({
    id: 'term-1',
    owner: 'local-admin',
    title: 'Terminal 1',
    pid: 123,
    rows: 24,
    cols: 80,
    status: 'running',
    output_cursor: 1234,
    replay_buffer_start_cursor: 0,
    idle_timeout_minutes: 60,
    created_at: new Date().toISOString(),
    last_active_at: new Date().toISOString(),
    exited_at: null,
    closed_at: null,
    close_reason: null,
  })),
  closeTerminal: vi.fn(async (id: string) => ({
    id,
    owner: 'local-admin',
    title: 'Terminal 1',
    pid: null,
    rows: 24,
    cols: 80,
    status: 'closed',
    output_cursor: 0,
    replay_buffer_start_cursor: 0,
    idle_timeout_minutes: 60,
    created_at: new Date().toISOString(),
    last_active_at: new Date().toISOString(),
    exited_at: null,
    closed_at: new Date().toISOString(),
    close_reason: 'user_closed',
  })),
}));

vi.mock('../src/terminal/TerminalPane', () => ({
  TerminalPane: ({ terminalId, initialCursor }: { terminalId: string; initialCursor?: number }) => (
    <div aria-label="Terminal">terminal pane for {terminalId} cursor {initialCursor}</div>
  ),
}));

describe('TerminalPage', () => {
  it('creates and switches to a terminal tab', async () => {
    const user = userEvent.setup();
    render(<TerminalPage />);

    await user.click(screen.getByRole('button', { name: /new terminal/i }));

    expect(await screen.findByRole('tab', { name: /terminal 1/i })).toBeTruthy();
    expect(screen.getByLabelText('Terminal')).toBeTruthy();
    expect(screen.getByText(/terminal pane for term-1 cursor 1234/i)).toBeTruthy();
  });
});
