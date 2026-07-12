import { QueryClient } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Vitest runs tests in Node; the frontend compiler intentionally omits Node types.
import { readFileSync } from 'node:fs';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));
const baseStyles = readFileSync('src/shared/styles/base.css', 'utf8');

const agent = {
  agent_id: 'alpha', display_name: 'Alpha', endpoint: 'https://10.0.0.4:8765',
  transport_profile_id: 'system-tls', enabled: true, connection_status: 'ready',
  workload_status: 'healthy', capabilities: ['observations.v2'],
};

const fleetAgent = {
  ...agent,
  summary: { observations: { total: 0, critical: 0 }, services: { total: 0, unhealthy: 0 } },
};

const discoveryJob = {
  job_id: 'scan-opaque', scope_id: 'lab', state: 'completed', total_targets: 1,
  checked_targets: 1, found_targets: 1,
};

const discoveryResult = {
  result_id: 'result-opaque', candidate_url: 'http://10.0.0.4:8765', ip: '10.0.0.4',
  port: 8765, transport_profile_id: 'trusted-lan-http', status: 'new',
  enrollment_status: 'enrollment_required',
};

function setMedia(width: number, reducedMotion = false) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? reducedMotion : query.includes('max-width: 767px') ? width < 768 : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, String(value)); },
  };
}

function storageText(storage: Storage) {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index) ?? '';
    return `${key}:${storage.getItem(key) ?? ''}`;
  }).join('|');
}

function installWorkflowApi(removeFails = false) {
  apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2', 'discovery.v2'] };
    if (path === '/api/v2/fleet/overview') return { collected_at: '2026-07-12T00:00:00Z', agents: [fleetAgent] };
    if (path === '/api/v2/agents/alpha' && !init?.method) return { agent: fleetAgent };
    if (path === '/api/v2/agents/alpha' && init?.method === 'DELETE') {
      if (removeFails) throw Object.assign(new Error('Agent is in use'), { code: 'agent_in_use' });
      return undefined;
    }
    if (path === '/api/v2/discovery/scopes') return { enabled: true, scopes: [{ id: 'lab', name: 'Lab rack', target_count: 1 }] };
    if (path === '/api/v2/discovery/jobs' && init?.method === 'POST') return { job: discoveryJob };
    if (path === '/api/v2/discovery/jobs/scan-opaque') return { job: discoveryJob };
    if (path === '/api/v2/discovery/jobs/scan-opaque/results') return { results: [discoveryResult] };
    if (path === '/api/v2/discovery/results/result-opaque') return { result: discoveryResult };
    throw new Error(`Unexpected request: ${path}`);
  });
}

async function keyboardActivate(user: ReturnType<typeof userEvent.setup>, element: HTMLElement) {
  element.focus();
  expect(document.activeElement).toBe(element);
  await user.keyboard('{Enter}');
}

describe('Fleet accessibility workflow', () => {
  beforeEach(() => {
    setMedia(1440);
    window.history.replaceState({}, '', '/agents/alpha/settings');
    Object.defineProperty(window, 'localStorage', { configurable: true, value: memoryStorage() });
    Object.defineProperty(window, 'sessionStorage', { configurable: true, value: memoryStorage() });
    apiRequest.mockReset();
    installWorkflowApi();
  });

  afterEach(() => cleanup());

  it('supports a keyboard Fleet result workflow with an announced result count', async () => {
    window.history.replaceState({}, '', '/fleet');
    const user = userEvent.setup();
    render(<App />);

    const row = await screen.findByRole('row', { name: /Alpha/ });
    expect(screen.getByRole('status').textContent).toBe('1 Agent displayed.');
    row.focus();
    await user.keyboard('{Enter}');

    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBeTruthy();
  });

  it('moves focus into the removal dialog and restores its trigger after keyboard Close or Escape', async () => {
    const user = userEvent.setup();
    render(<App />);

    const trigger = await screen.findByRole('button', { name: 'Remove from Manager' });
    await keyboardActivate(user, trigger);

    const dialog = await screen.findByRole('dialog', { name: 'Remove from Manager' });
    expect(within(dialog).getAllByRole('button', { name: 'Close' })).toContain(document.activeElement);
    await user.keyboard('{Enter}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(trigger);

    await keyboardActivate(user, trigger);
    await screen.findByRole('dialog', { name: 'Remove from Manager' });
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it('completes Add, Discovery, Fleet, detail, and Remove paths using only keyboard activation', async () => {
    installWorkflowApi(true);
    window.history.replaceState({}, '', '/fleet');
    const user = userEvent.setup();
    render(<App />);

    await keyboardActivate(user, await screen.findByRole('link', { name: 'Add agent' }));
    expect(await screen.findByRole('heading', { name: 'Add agent' })).toBe(document.activeElement);
    expect(screen.getByRole('alert').textContent).toContain('Trusted-LAN');

    await keyboardActivate(user, screen.getByRole('link', { name: 'Return to Fleet' }));
    await keyboardActivate(user, await screen.findByRole('link', { name: 'Discover agents' }));
    expect(await screen.findByRole('heading', { name: 'Discovery' })).toBe(document.activeElement);
    const scope = await screen.findByLabelText('Discovery scope');
    scope.focus();
    await user.selectOptions(scope, 'lab');
    await keyboardActivate(user, screen.getByRole('button', { name: 'Start discovery' }));
    expect((await screen.findByText('Discovery completed')).closest('[aria-live="polite"]')).toBeTruthy();

    await keyboardActivate(user, screen.getByRole('link', { name: 'Enroll candidate' }));
    expect(await screen.findByRole('heading', { name: 'Add agent' })).toBe(document.activeElement);
    expect(window.location.search).toBe('?discoveryResult=result-opaque');
    await waitFor(() => expect((screen.getByLabelText('Agent URL') as HTMLInputElement).value).toBe(discoveryResult.candidate_url));

    await keyboardActivate(user, screen.getByRole('link', { name: 'Return to Fleet' }));
    const row = await screen.findByRole('row', { name: /Alpha/ });
    await keyboardActivate(user, row);
    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBe(document.activeElement);
    await keyboardActivate(user, screen.getByRole('link', { name: 'Settings' }));
    await keyboardActivate(user, await screen.findByRole('button', { name: 'Remove from Manager' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove from Manager' });
    await keyboardActivate(user, within(dialog).getByRole('button', { name: 'Remove from Manager' }));
    expect((await within(dialog).findByRole('alert')).textContent).toContain('currently in use');
  });

  it.each([375, 768, 1024, 1440])('keeps the Fleet operable at the %ipx responsive contract', async (width) => {
    setMedia(width);
    window.history.replaceState({}, '', '/fleet');
    render(<App />);

    if (width === 375) {
      const cards = await screen.findByRole('list', { name: 'Fleet agents' });
      expect(within(cards).getByRole('link', { name: 'Open Alpha' })).toBeTruthy();
      expect(screen.queryByRole('table', { name: 'Fleet agents' })).toBeNull();
    } else {
      const table = await screen.findByRole('table', { name: 'Fleet agents' });
      expect(within(table).getByRole('link', { name: 'Open Alpha' })).toBeTruthy();
      expect(screen.queryByRole('list', { name: 'Fleet agents' })).toBeNull();
    }
  });

  it('remains keyboard-operable when reduced motion is preferred', async () => {
    setMedia(1440, true);
    window.history.replaceState({}, '', '/fleet');
    const user = userEvent.setup();
    render(<App />);

    await keyboardActivate(user, await screen.findByRole('row', { name: /Alpha/ }));
    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBe(document.activeElement);
    expect(baseStyles).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*animation-duration: 0\.01ms !important/);
  });

  it('restores a detail deep link after a fresh App mount', async () => {
    window.history.replaceState({}, '', '/agents/alpha/overview');
    const first = render(<App />);
    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBeTruthy();
    first.unmount();

    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Alpha' })).toBe(document.activeElement);
    expect(window.location.pathname).toBe('/agents/alpha/overview');
  });

  it('does not expose write-only enrollment response material in observable browser surfaces', async () => {
    const secret = 'secret-like-write-only-value';
    let observedClient: QueryClient | undefined;
    const captureClient = (client: QueryClient) => { observedClient = client; };
    const originalMount = QueryClient.prototype.mount;
    const mount = vi.spyOn(QueryClient.prototype, 'mount').mockImplementation(function (this: QueryClient) {
      captureClient(this);
      return originalMount.call(this);
    });
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['agent-registry.v2'] };
      if (path === '/api/v2/agent-enrollments' && init?.method === 'POST') return {
        enrollment_id: 'job-opaque', state: 'awaiting_cli', pending_token: secret,
      };
      if (path === '/api/v2/agent-enrollments/job-opaque') return {
        enrollment_id: 'job-opaque', state: 'awaiting_cli', preview: { phases: {} },
      };
      throw new Error(`Unexpected request: ${path}`);
    });
    window.history.replaceState({}, '', '/agents/new');
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText('Display name'), 'Alpha');
    await user.type(screen.getByLabelText('Agent URL'), 'https://10.0.0.4:8765');
    await user.type(screen.getByLabelText('SSH user'), 'edaops');
    await user.type(screen.getByLabelText('SSH host'), '10.0.0.4');
    await user.click(screen.getByRole('button', { name: 'Start enrollment' }));
    await screen.findByText('Waiting for CLI');

    expect(document.documentElement.textContent).not.toContain(secret);
    expect(window.location.href).not.toContain(secret);
    expect(storageText(window.sessionStorage)).not.toContain(secret);
    expect(storageText(window.localStorage)).not.toContain(secret);
    expect(JSON.stringify(observedClient?.getQueryCache().getAll().map((query) => query.state.data))).not.toContain(secret);
    mount.mockRestore();
  });
});
