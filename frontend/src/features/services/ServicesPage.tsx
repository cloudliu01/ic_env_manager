import { useServiceAction, useServices } from './queries';
import { ServiceTarget } from './types';

export function ServicesPage({ target }: { target: ServiceTarget }) {
  const supportsServices = target.capabilities.includes('services.v1');
  const services = useServices(target.agentId, supportsServices);
  const start = useServiceAction(target.agentId, 'start');
  const stop = useServiceAction(target.agentId, 'stop');

  return <section className="feature-page">
    <h1 tabIndex={-1}>Services</h1>
    {!supportsServices ? <p role="status">This Agent does not support services.</p> : null}
    {services.isPending ? <p role="status">Loading services…</p> : null}
    {services.isError ? <p role="alert">Unable to load services.</p> : null}
    {services.data?.map((service) => <article key={service.id}>
      <h2>{service.name}</h2><p>Status: {service.status}</p>
      <button type="button" onClick={() => start.mutate(service.id)} disabled={!supportsServices || start.isPending}>Start</button>
      <button type="button" onClick={() => stop.mutate(service.id)} disabled={!supportsServices || stop.isPending}>Stop</button>
    </article>)}
  </section>;
}
