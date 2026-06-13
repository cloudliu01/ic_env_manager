import { apiClient } from './client';

export type LoginResponse = {
  actor: string;
  token_type: 'bearer';
};

export async function login(token: string): Promise<LoginResponse> {
  const response = await apiClient.request<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ token }),
  });
  apiClient.setToken(token);
  return response;
}

export async function logout(): Promise<void> {
  await apiClient.request<void>('/api/auth/logout', { method: 'POST' });
  apiClient.setToken(null);
}
