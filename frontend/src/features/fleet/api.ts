import { apiClient } from '../../shared/api/client';
import { Agent } from '../agent-registry/types';

export type FleetOverview = {
  collected_at: string;
  agents: Agent[];
};

export function getFleetOverview(signal?: AbortSignal): Promise<FleetOverview> {
  return apiClient.request<FleetOverview>('/api/v2/fleet/overview', { signal });
}
