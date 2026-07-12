import { apiClient } from '../../shared/api/client';

export type ManagerCredential = {
  credential_id: string;
  manager_id: string;
  state: 'pending' | 'active' | 'revoked';
  pending_expires_at: string | null;
  created_at: string;
  activated_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
};

export async function listManagerCredentials(signal?: AbortSignal): Promise<ManagerCredential[]> {
  return (await apiClient.request<{ credentials: ManagerCredential[] }>('/api/v2/manager-credentials', { signal })).credentials;
}

export function revokeManagerCredential(credentialId: string): Promise<{ credential_id: string; state: string }> {
  return apiClient.request(`/api/v2/manager-credentials/${encodeURIComponent(credentialId)}`, { method: 'DELETE' });
}
