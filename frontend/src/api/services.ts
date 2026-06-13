import { apiClient } from './client';

export type ServiceSummary = {
  id: string;
  name: string;
  status: string;
  health_status: string;
  allowed_operations: string[];
};

export async function listServices(): Promise<ServiceSummary[]> {
  const response = await apiClient.request<{ services: ServiceSummary[] }>('/api/services');
  return response.services;
}

export async function startService(id: string) {
  return apiClient.request(`/api/services/${id}/start`, { method: 'POST' });
}

export async function stopService(id: string) {
  return apiClient.request(`/api/services/${id}/stop`, { method: 'POST' });
}
