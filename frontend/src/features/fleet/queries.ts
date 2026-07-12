import { useQuery } from '@tanstack/react-query';
import { getFleetOverview } from './api';

export const fleetKeys = {
  all: ['fleet'] as const,
  overview: () => ['fleet', 'overview'] as const,
};

function pollingInterval() {
  return document.visibilityState === 'visible' ? 30_000 : false;
}

export function useFleetOverview() {
  return useQuery({
    queryKey: fleetKeys.overview(),
    queryFn: ({ signal }) => getFleetOverview(signal),
    refetchInterval: pollingInterval,
    refetchIntervalInBackground: false,
  });
}
