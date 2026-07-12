import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFleetOverview } from './api';

export const fleetKeys = {
  all: ['fleet'] as const,
  overview: () => ['fleet', 'overview'] as const,
};

function usePollingInterval() {
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');
  useEffect(() => {
    const updateVisibility = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', updateVisibility);
    return () => document.removeEventListener('visibilitychange', updateVisibility);
  }, []);
  return visible ? 30_000 : false;
}

export function useFleetOverview() {
  const refetchInterval = usePollingInterval();
  return useQuery({
    queryKey: fleetKeys.overview(),
    queryFn: ({ signal }) => getFleetOverview(signal),
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}
