import { createContext, PropsWithChildren, useContext } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../shared/api/client';

export type Runtime = {
  mode: 'agent' | 'manager';
  capabilities: string[];
};

const RuntimeContext = createContext<Runtime | null>(null);

export async function getRuntime(signal?: AbortSignal): Promise<Runtime> {
  return apiClient.request<Runtime>('/api/v2/runtime', { signal });
}

export function RuntimeProvider({ children }: PropsWithChildren) {
  const query = useQuery({
    queryKey: ['runtime'],
    queryFn: ({ signal }) => getRuntime(signal),
    staleTime: Infinity,
    retry: false,
  });

  if (query.isPending) {
    return <main><p role="status">Loading runtime…</p></main>;
  }
  if (query.isError) {
    return (
      <main>
        <h1>Runtime unavailable</h1>
        <p role="alert">The application mode could not be loaded.</p>
        <button type="button" onClick={() => void query.refetch()}>Retry</button>
      </main>
    );
  }
  return <RuntimeContext.Provider value={query.data}>{children}</RuntimeContext.Provider>;
}

export function useRuntime(): Runtime {
  const runtime = useContext(RuntimeContext);
  if (!runtime) {
    throw new Error('useRuntime must be used within RuntimeProvider');
  }
  return runtime;
}
