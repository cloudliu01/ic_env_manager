import { useEffect } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppRoutes } from '../src/pages/AppRoutes';

const terminalMounts = vi.hoisted(() => vi.fn());
const CAPABILITIES = ['services.v1', 'terminals.v1', 'audit.v1', 'monitoring.snapshot.v1'];

vi.mock('../src/auth/session', () => ({
  loadSessionToken: vi.fn(() => 'secret-token'),
}));

vi.mock('../src/api/client', () => ({
  apiClient: {
    setToken: vi.fn(),
    request: vi.fn(async (path: string) => {
      if (path === '/api/agents') {
        return { agents: [{ id: 'agent-a', name: 'Alpha', status: 'ready', enabled: true, capabilities: CAPABILITIES }] };
      }
      return { status: 'ready' };
    }),
  },
}));

vi.mock('../src/pages/TerminalPage', () => ({
  TerminalPage: ({ visible = true }: { visible?: boolean }) => {
    useEffect(() => {
      terminalMounts();
    }, []);
    return <div aria-label="Terminal page">Terminal visible: {String(visible)}</div>;
  },
}));

vi.mock('../src/pages/ServiceListPage', () => ({
  ServiceListPage: () => <div>Services page</div>,
}));

vi.mock('../src/pages/MetricsPage', () => ({
  MetricsPage: () => <div>Metrics page</div>,
}));

vi.mock('../src/pages/AuditStatusPage', () => ({
  AuditStatusPage: () => <div>Audit page</div>,
}));

describe('AppRoutes terminal navigation', () => {
  beforeEach(() => {
    terminalMounts.mockClear();
  });

  it('keeps the terminal page mounted when switching to another section and back', async () => {
    const user = userEvent.setup();
    render(<AppRoutes />);
    await screen.findByLabelText('Active agent');

    await user.click(screen.getByRole('button', { name: 'Terminal' }));
    expect(screen.getByLabelText('Terminal page').textContent).toContain('true');
    expect(terminalMounts).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: 'Services' }));
    expect(screen.getByText('Services page')).toBeTruthy();
    expect(screen.getByLabelText('Terminal page').textContent).toContain('false');

    await user.click(screen.getByRole('button', { name: 'Terminal' }));
    expect(screen.getByLabelText('Terminal page').textContent).toContain('true');
    expect(terminalMounts).toHaveBeenCalledTimes(1);
  });
});
