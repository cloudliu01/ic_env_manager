import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesPage } from '../src/features/services/ServicesPage';

const listServices = vi.hoisted(() => vi.fn(async () => [{ id: 'demo', name: 'Beta service', status: 'configured', health_status: 'unknown', allowed_operations: ['start', 'stop'] }]));

vi.mock('../src/features/services/api', () => ({
  listServices,
  startService: vi.fn(async () => ({})),
  stopService: vi.fn(async () => ({})),
}));

describe('ServicesPage', () => {
  it('loads services for its explicit manager agent target', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<QueryClientProvider client={client}><ServicesPage target={{ agentId: 'agent-b', name: 'Beta', capabilities: ['services.v1'] }} /></QueryClientProvider>);
    expect(await screen.findByText('Beta service')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start' })).toBeTruthy();
    expect(Array.from(container.querySelectorAll('h1,h2,h3')).map((heading) => heading.tagName)).toEqual(['H1', 'H2']);
    expect(listServices).toHaveBeenCalledWith('agent-b', expect.anything());
  });
});
