import { apiClient } from '../../shared/api/client';

export type DiscoveryScope = { id: string; name: string; cidr: string; endpoints: Array<{ port: number; transport_profile_id: string }>; target_count: number };
export type DiscoveryJob = { job_id: string; scope_id: string; state: string; total_targets: number; checked_targets: number; found_targets: number; error_code?: string | null };
export type DiscoveryResult = { result_id: string; candidate_url: string; ip: string; port: number; transport_profile_id: string; status: string; enrollment_status: string; error_code?: string | null };

export function listDiscoveryScopes(signal?: AbortSignal) {
  return apiClient.request<{ enabled: boolean; scopes: DiscoveryScope[] }>('/api/v2/discovery/scopes', { signal });
}
export function startDiscovery(scopeId: string) {
  return apiClient.request<{ job: DiscoveryJob }>('/api/v2/discovery/jobs', { method: 'POST', body: JSON.stringify({ scope_id: scopeId }) });
}
export function getDiscoveryJob(jobId: string, signal?: AbortSignal) {
  return apiClient.request<{ job: DiscoveryJob }>(`/api/v2/discovery/jobs/${encodeURIComponent(jobId)}`, { signal });
}
export function cancelDiscoveryJob(jobId: string) {
  return apiClient.request<{ job: DiscoveryJob }>(`/api/v2/discovery/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
}
export function listDiscoveryResults(jobId: string, signal?: AbortSignal) {
  return apiClient.request<{ results: DiscoveryResult[] }>(`/api/v2/discovery/jobs/${encodeURIComponent(jobId)}/results`, { signal });
}
export function getDiscoveryResult(resultId: string, signal?: AbortSignal) {
  return apiClient.request<{ result: DiscoveryResult }>(`/api/v2/discovery/results/${encodeURIComponent(resultId)}`, { signal });
}
