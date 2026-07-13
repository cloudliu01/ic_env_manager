import { apiClient } from '../../shared/api/client';
import { Agent } from './types';

export type EnrollmentJob = {
  enrollment_id: string;
  state: string;
  expires_at?: string;
  last_error_code?: string | null;
  residual_warning?: string | null;
  cli?: { argv: string[]; display: string };
  preview?: {
    agent?: {
      agent_id?: string;
      instance_id?: string | null;
      name?: string | null;
      endpoint?: string;
      transport_profile_id?: string;
      transport_security?: string | null;
      api_version?: string;
      agent_version?: string;
      capabilities?: string[];
      summary?: Record<string, unknown> | null;
    } | null;
    phases?: Record<string, { status?: string; code?: string | null }>;
  };
};

export type EnrollmentInput = {
  base_url: string;
  display_name?: string;
  transport_profile_id: string;
  ssh: { user: string; host: string; port: number };
  discovery_result_id?: string;
};

export function createEnrollment(input: EnrollmentInput, signal?: AbortSignal) {
  return apiClient.request<EnrollmentJob>('/api/v2/agent-enrollments', { method: 'POST', body: JSON.stringify(input), signal });
}

export function getEnrollment(enrollmentId: string, signal?: AbortSignal) {
  return apiClient.request<EnrollmentJob>(`/api/v2/agent-enrollments/${encodeURIComponent(enrollmentId)}`, { signal });
}

export function cancelEnrollment(enrollmentId: string) {
  return apiClient.request<EnrollmentJob>(`/api/v2/agent-enrollments/${encodeURIComponent(enrollmentId)}/cancel`, { method: 'POST' });
}

export function saveEnrolledAgent(enrollmentId: string, displayName?: string) {
  return apiClient.request<{ agent: Agent }>('/api/v2/agents', { method: 'POST', body: JSON.stringify({ enrollment_id: enrollmentId, display_name: displayName }) });
}

export function validateLegacyAgent(input: Pick<EnrollmentInput, 'base_url' | 'transport_profile_id'> & { token: string }) {
  return apiClient.request<EnrollmentJob>('/api/v2/agents/validate', { method: 'POST', body: JSON.stringify(input) });
}

export function updateAgent(agentId: string, body: Partial<Pick<Agent, 'display_name' | 'enabled'>> & { base_url?: string; transport_profile_id?: string; legacy_token?: string }) {
  return apiClient.request<{ agent: Agent }>(`/api/v2/agents/${encodeURIComponent(agentId)}`, { method: 'PUT', body: JSON.stringify(body) });
}

export function probeAgent(agentId: string) {
  return apiClient.request<{ agent: Agent }>(`/api/v2/agents/${encodeURIComponent(agentId)}/probe`, { method: 'POST' });
}

export function removeAgent(agentId: string, localOnly: boolean) {
  const suffix = localOnly ? '?local_only=true' : '';
  return apiClient.request<void>(`/api/v2/agents/${encodeURIComponent(agentId)}${suffix}`, {
    method: 'DELETE', body: localOnly ? JSON.stringify({ confirm_remote_residual: true }) : undefined,
  });
}

export function startCredentialRotation(agentId: string, ssh: EnrollmentInput['ssh']) {
  return apiClient.request<{ rotation: EnrollmentJob }>(`/api/v2/agents/${encodeURIComponent(agentId)}/credential-rotation`, {
    method: 'POST', body: JSON.stringify({ action: 'start', ssh }),
  });
}

export function consumeCredentialRotation(agentId: string, enrollmentId: string) {
  return apiClient.request<{ rotation: EnrollmentJob }>(`/api/v2/agents/${encodeURIComponent(agentId)}/credential-rotation`, {
    method: 'POST', body: JSON.stringify({ action: 'consume', enrollment_id: enrollmentId }),
  });
}
