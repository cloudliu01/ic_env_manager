import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/app/App';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/shared/api/client', () => ({ apiClient: { request: apiRequest } }));

describe('Discovery flow', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/discovery');
    apiRequest.mockReset();
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['discovery.v2'] };
      if (path === '/api/v2/discovery/scopes') return { enabled: true, scopes: [{ id: 'lab', name: 'Lab rack', target_count: 2 }] };
      if (path === '/api/v2/discovery/jobs' && init?.method === 'POST') return { job: { job_id: 'scan-opaque', scope_id: 'lab', state: 'running', total_targets: 2, checked_targets: 1, found_targets: 1 } };
      if (path === '/api/v2/discovery/jobs/scan-opaque') return { job: { job_id: 'scan-opaque', scope_id: 'lab', state: 'completed', total_targets: 2, checked_targets: 2, found_targets: 1 } };
      if (path === '/api/v2/discovery/jobs/scan-opaque/results') return { results: [{ result_id: 'result-opaque', candidate_url: 'http://10.0.0.4:8765', ip: '10.0.0.4', port: 8765, transport_profile_id: 'eda-http', status: 'new', enrollment_status: 'enrollment_required' }] };
      throw new Error(`Unexpected request: ${path}`);
    });
  });
  afterEach(() => cleanup());

  it('starts only a named scope, shows progress and hands a result to Add Agent by opaque id', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('option', { name: /Lab rack/ });
    await user.selectOptions(screen.getByLabelText('Discovery scope'), 'lab');
    await user.click(screen.getByRole('button', { name: 'Start discovery' }));
    expect(await screen.findByText('2 checked of 2')).toBeTruthy();
    expect(screen.getByText('1 found')).toBeTruthy();
    expect(screen.getByText(/10\.0\.0\.4:8765/)).toBeTruthy();
    await user.click(screen.getByRole('link', { name: 'Enroll candidate' }));
    expect(window.location.pathname).toBe('/agents/new');
    expect(window.location.search).toBe('?discoveryResult=result-opaque');
  });

  it('refetches partial results when a discovery job reaches a result-bearing terminal state', async () => {
    let jobReads = 0;
    let resultReads = 0;
    apiRequest.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/api/v2/runtime') return { mode: 'manager', capabilities: ['discovery.v2'] };
      if (path === '/api/v2/discovery/scopes') return { enabled: true, scopes: [{ id: 'lab', name: 'Lab rack', target_count: 2 }] };
      if (path === '/api/v2/discovery/jobs' && init?.method === 'POST') return { job: { job_id: 'scan-opaque', scope_id: 'lab', state: 'running', total_targets: 2, checked_targets: 1, found_targets: 1 } };
      if (path === '/api/v2/discovery/jobs/scan-opaque') return { job: { job_id: 'scan-opaque', scope_id: 'lab', state: ++jobReads === 1 ? 'running' : 'completed', total_targets: 2, checked_targets: jobReads === 1 ? 1 : 2, found_targets: 1 } };
      if (path === '/api/v2/discovery/jobs/scan-opaque/results') return { results: ++resultReads === 1 ? [] : [{ result_id: 'result-opaque', candidate_url: 'http://10.0.0.4:8765', ip: '10.0.0.4', port: 8765, transport_profile_id: 'eda-http', status: 'new', enrollment_status: 'enrollment_required' }] };
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('option', { name: /Lab rack/ });
    await user.selectOptions(screen.getByLabelText('Discovery scope'), 'lab');
    await user.click(screen.getByRole('button', { name: 'Start discovery' }));

    await new Promise((resolve) => window.setTimeout(resolve, 2200));
    expect(await screen.findByRole('link', { name: 'Enroll candidate' }, { timeout: 2500 })).toBeTruthy();
  });
});
