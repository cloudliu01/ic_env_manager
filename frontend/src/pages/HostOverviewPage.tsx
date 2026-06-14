import { useEffect, useRef, useState } from 'react';
import { apiClient } from '../api/client';
import { useActiveAgent } from '../agents/AgentContext';

export function HostOverviewPage() {
  const [ready, setReady] = useState<string>('unknown');
  const { activeAgentId } = useActiveAgent();
  const requestGeneration = useRef(0);

  useEffect(() => {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    if (!activeAgentId) {
      setReady('no active agent');
      return;
    }

    const controller = new AbortController();
    setReady('unknown');
    apiClient
      .request<{ status: string }>(`/api/agents/${encodeURIComponent(activeAgentId)}/readyz`, { signal: controller.signal })
      .then((response) => {
        if (requestGeneration.current === generation) {
          setReady(response.status);
        }
      })
      .catch((err: Error) => {
        if (requestGeneration.current === generation && err.name !== 'AbortError') {
          setReady('unavailable');
        }
      });

    return () => controller.abort();
  }, [activeAgentId]);

  return (
    <section>
      <h2>Host Overview</h2>
      <p>Agent readiness: {ready}</p>
    </section>
  );
}
