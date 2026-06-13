import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AuditStatusPage } from '../src/pages/AuditStatusPage';

vi.mock('../src/api/audit', () => ({
  listAuditEvents: vi.fn(async () => ({
    events: [
      {
        id: 1,
        timestamp: '2026-06-13T00:00:00Z',
        operation: 'service.start',
        target_type: 'service',
        target_id: 'demo',
        result: 'success',
      },
    ],
  })),
}));

describe('AuditStatusPage', () => {
  it('renders lifecycle and operation audit records without secret fields', async () => {
    render(<AuditStatusPage />);
    expect(await screen.findByText('service.start')).toBeTruthy();
    expect(screen.getByText('service')).toBeTruthy();
    expect(screen.queryByText(/token|password|private_key/i)).toBeNull();
  });
});
