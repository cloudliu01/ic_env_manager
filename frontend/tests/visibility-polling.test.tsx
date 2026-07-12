import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const useQuery = vi.hoisted(() => vi.fn(() => ({ data: undefined })));

vi.mock('@tanstack/react-query', () => ({ useQuery }));

import { useFleetOverview } from '../src/features/fleet/queries';

function FleetPollingProbe() {
  useFleetOverview();
  return null;
}

describe('visibility-aware polling', () => {
  afterEach(() => {
    cleanup();
    useQuery.mockClear();
  });

  it('reconfigures fleet polling when the document becomes visible', async () => {
    let visibilityState: DocumentVisibilityState = 'hidden';
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibilityState });

    render(<FleetPollingProbe />);
    expect(useQuery).toHaveBeenCalledTimes(1);
    const calls = useQuery.mock.calls as unknown as Array<[{ refetchInterval: number | false }]>;
    const firstOptions = calls[0]![0];
    expect(firstOptions.refetchInterval).toBe(false);

    visibilityState = 'visible';
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => expect(useQuery).toHaveBeenCalledTimes(2));
    const visibleOptions = calls[1]![0];
    expect(visibleOptions.refetchInterval).toBe(30_000);
  });
});
