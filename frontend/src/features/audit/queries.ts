import { useQuery } from '@tanstack/react-query';
import { listAuditEvents } from './api';
import { AuditFilters } from './types';

export const auditKeys = { list: (agentId: string, filters: AuditFilters) => ['agents', agentId, 'audit', filters] as const };
export function useAuditEvents(agentId: string, filters: AuditFilters, enabled: boolean) {
  return useQuery({ queryKey: auditKeys.list(agentId, filters), queryFn: ({ signal }) => listAuditEvents(agentId, filters, signal), enabled });
}
