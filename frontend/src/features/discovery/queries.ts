import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DiscoveryJob, getDiscoveryJob, getDiscoveryResult, listDiscoveryResults, listDiscoveryScopes } from './api';

export const discoveryKeys = { scopes: () => ['discovery', 'scopes'] as const, job: (id: string) => ['discovery', 'job', id] as const, results: (id: string) => ['discovery', 'results', id] as const, result: (id: string) => ['discovery', 'result', id] as const };
const terminal = new Set(['completed', 'failed', 'cancelled']);
function useVisible() { const [visible, setVisible] = useState(() => document.visibilityState === 'visible'); useEffect(() => { const update = () => setVisible(document.visibilityState === 'visible'); document.addEventListener('visibilitychange', update); return () => document.removeEventListener('visibilitychange', update); }, []); return visible; }
export function useDiscoveryScopes() { return useQuery({ queryKey: discoveryKeys.scopes(), queryFn: ({ signal }) => listDiscoveryScopes(signal), retry: false }); }
export function useDiscoveryJob(jobId?: string) { const visible = useVisible(); return useQuery({ queryKey: discoveryKeys.job(jobId ?? ''), queryFn: ({ signal }) => getDiscoveryJob(jobId!, signal), enabled: Boolean(jobId), retry: false, refetchInterval: (query) => visible && query.state.data && !terminal.has((query.state.data as { job: DiscoveryJob }).job.state) ? 2_000 : false, refetchIntervalInBackground: false }); }
export function useDiscoveryResults(jobId?: string, enabled = false) { return useQuery({ queryKey: discoveryKeys.results(jobId ?? ''), queryFn: ({ signal }) => listDiscoveryResults(jobId!, signal), enabled: Boolean(jobId) && enabled, retry: false }); }
export function useDiscoveryResult(resultId?: string) { return useQuery({ queryKey: discoveryKeys.result(resultId ?? ''), queryFn: ({ signal }) => getDiscoveryResult(resultId!, signal), enabled: Boolean(resultId), retry: false }); }
