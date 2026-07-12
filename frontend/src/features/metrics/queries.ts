import { useQuery } from '@tanstack/react-query';
import { getMonitoringSnapshot } from './api';

export const metricsKeys = { snapshot: (agentId: string) => ['agents', agentId, 'metrics'] as const };

export function useMonitoringSnapshot(agentId: string, enabled: boolean) {
  return useQuery({
    queryKey: metricsKeys.snapshot(agentId),
    queryFn: ({ signal }) => getMonitoringSnapshot(agentId, { signal }),
    enabled,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
  });
}
