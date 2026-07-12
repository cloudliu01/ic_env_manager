import { useQuery } from '@tanstack/react-query';
import { listObservations } from './api';

export const observationKeys = { list: (agentId: string, status?: string) => ['agents', agentId, 'observations', status] as const };
export function useObservations(agentId: string, status: string | undefined, enabled: boolean) {
  return useQuery({ queryKey: observationKeys.list(agentId, status), queryFn: ({ signal }) => listObservations(agentId, status, signal), enabled, refetchInterval: 30_000, refetchIntervalInBackground: false });
}
