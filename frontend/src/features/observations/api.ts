import { apiClient } from '../../shared/api/client';
import { ObservationPage } from './types';

export function listObservations(agentId: string, status?: string, signal?: AbortSignal): Promise<ObservationPage> {
  const query = new URLSearchParams();
  if (agentId === 'local') query.set('include_stale', 'true');
  if (status) query.set('status', status);
  const suffix = query.size ? `?${query}` : '';
  const path = agentId === 'local' ? `/api/v2/observations${suffix}` : `/api/v2/agents/${encodeURIComponent(agentId)}/observations${suffix}`;
  return apiClient.request<ObservationPage>(path, { signal });
}
