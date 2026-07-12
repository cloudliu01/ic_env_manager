import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listServices, startService, stopService } from './api';

export const serviceKeys = { list: (agentId: string) => ['agents', agentId, 'services'] as const };

export function useServices(agentId: string, enabled: boolean) {
  return useQuery({ queryKey: serviceKeys.list(agentId), queryFn: ({ signal }) => listServices(agentId, { signal }), enabled });
}

export function useServiceAction(agentId: string, action: 'start' | 'stop') {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (serviceId: string) => action === 'start' ? startService(agentId, serviceId) : stopService(agentId, serviceId),
    retry: false,
    onSuccess: () => client.invalidateQueries({ queryKey: serviceKeys.list(agentId) }),
  });
}
