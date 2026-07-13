import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../../shared/api/client';

export type TransportProfileOption = {
  id: string;
  type: 'verified_tls' | 'trusted_lan_http';
  security_label: string;
  warning: string | null;
};

export const transportProfileKeys = { all: ['transport-profiles'] as const };

export function useTransportProfiles() {
  return useQuery({
    queryKey: transportProfileKeys.all,
    queryFn: ({ signal }) => apiClient.request<{ profiles: TransportProfileOption[] }>('/api/v2/transport-profiles', { signal }),
    staleTime: Infinity,
  });
}
