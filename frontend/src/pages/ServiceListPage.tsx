import { useEffect, useRef, useState } from 'react';
import { agentSupports, useActiveAgent } from '../agents/AgentStateContext';
import { listServices, ServiceSummary, startService, stopService } from '../api/services';

export function ServiceListPage() {
  const [services, setServices] = useState<ServiceSummary[]>([]);
  const { activeAgent, activeAgentId } = useActiveAgent();
  const supportsServices = agentSupports(activeAgent, 'services.v1');
  const requestGeneration = useRef(0);

  async function refresh(agentId = activeAgentId, signal?: AbortSignal) {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    if (!agentId || !supportsServices) {
      setServices([]);
      return;
    }

    try {
      const nextServices = await listServices(agentId, signal ? { signal } : undefined);
      if (requestGeneration.current === generation) {
        setServices(nextServices);
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError' && requestGeneration.current === generation) {
        setServices([]);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refresh(activeAgentId, controller.signal);
    return () => controller.abort();
  }, [activeAgentId]);

  async function handleStart(serviceId: string) {
    if (!activeAgentId || !supportsServices) {
      return;
    }
    await startService(activeAgentId, serviceId);
    await refresh(activeAgentId);
  }

  async function handleStop(serviceId: string) {
    if (!activeAgentId || !supportsServices) {
      return;
    }
    await stopService(activeAgentId, serviceId);
    await refresh(activeAgentId);
  }

  return (
    <section>
      <h1 tabIndex={-1}>Services</h1>
      {!activeAgentId ? <p>No active agent selected.</p> : null}
      {activeAgentId && !supportsServices ? <p>Selected agent does not support services.</p> : null}
      {services.map((service) => (
        <article key={service.id}>
          <h2>{service.name}</h2>
          <p>Status: {service.status}</p>
          <button type="button" onClick={() => void handleStart(service.id)} disabled={!supportsServices}>Start</button>
          <button type="button" onClick={() => void handleStop(service.id)} disabled={!supportsServices}>Stop</button>
        </article>
      ))}
    </section>
  );
}
