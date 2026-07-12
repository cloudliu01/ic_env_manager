import { useEffect, useRef, useState } from 'react';
import { supportsCapability, useStandaloneAgent } from '../agents/StandaloneAgentContext';
import { listServices, ServiceSummary, startService, stopService } from '../api/services';

export function ServiceListPage() {
  const [services, setServices] = useState<ServiceSummary[]>([]);
  const { agentId, capabilities } = useStandaloneAgent();
  const supportsServices = supportsCapability(capabilities, 'services.v1');
  const requestGeneration = useRef(0);

  async function refresh(signal?: AbortSignal) {
    const generation = requestGeneration.current + 1;
    requestGeneration.current = generation;
    if (!supportsServices) {
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
    void refresh(controller.signal);
    return () => controller.abort();
  }, [agentId, supportsServices]);

  async function handleStart(serviceId: string) {
    if (!supportsServices) {
      return;
    }
    await startService(agentId, serviceId);
    await refresh();
  }

  async function handleStop(serviceId: string) {
    if (!supportsServices) {
      return;
    }
    await stopService(agentId, serviceId);
    await refresh();
  }

  return (
    <section>
      <h1 tabIndex={-1}>Services</h1>
      {!supportsServices ? <p>This Agent does not support services.</p> : null}
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
