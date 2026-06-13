import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MetricsPage } from '../src/pages/MetricsPage';

describe('MetricsPage', () => {
  it('shows scrape endpoint and allowlist guidance', () => {
    render(<MetricsPage />);
    expect(screen.getByText('/metrics')).toBeTruthy();
    expect(screen.getByText(/network allowlist/i)).toBeTruthy();
  });
});
