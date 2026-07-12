import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ServiceListPage } from '../src/pages/ServiceListPage';

vi.mock('../src/agents/StandaloneAgentContext', () => ({
  useStandaloneAgent: () => ({ agentId: 'local', name: 'Build node', capabilities: ['services.v1'] }),
  supportsCapability: (capabilities: string[], capability: string) => capabilities.includes(capability),
}));

vi.mock('../src/api/services', () => ({
  listServices: vi.fn(async () => [{ id: 'demo', name: 'Demo', status: 'configured', health_status: 'unknown', allowed_operations: ['start', 'stop'] }]),
  startService: vi.fn(async () => ({})),
  stopService: vi.fn(async () => ({})),
}));

describe('ServiceListPage', () => {
  it('renders configured services', async () => {
    const { container } = render(<ServiceListPage />);
    expect(await screen.findByText('Demo')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start' })).toBeTruthy();
    expect(Array.from(container.querySelectorAll('h1,h2,h3')).map((heading) => heading.tagName)).toEqual(['H1', 'H2']);
  });
});
