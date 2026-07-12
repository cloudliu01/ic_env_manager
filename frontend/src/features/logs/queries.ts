import { useQuery } from '@tanstack/react-query';
import { listLogs, tailLog } from './api';

export const logKeys = { list: (agentId: string) => ['agents', agentId, 'logs'] as const, tail: (agentId: string, logId: string) => ['agents', agentId, 'logs', logId, 'tail', 100] as const };
export function useLogs(agentId: string, enabled: boolean) { return useQuery({ queryKey: logKeys.list(agentId), queryFn: ({ signal }) => listLogs(agentId, signal), enabled }); }
export function useLogTail(agentId: string, logId: string | null) { return useQuery({ queryKey: logKeys.tail(agentId, logId ?? ''), queryFn: ({ signal }) => tailLog(agentId, logId ?? '', signal), enabled: Boolean(logId) }); }
