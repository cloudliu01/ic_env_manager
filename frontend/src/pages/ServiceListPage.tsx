import { useEffect, useState } from 'react';
import { listServices, ServiceSummary, startService, stopService } from '../api/services';

export function ServiceListPage() {
  const [services, setServices] = useState<ServiceSummary[]>([]);

  async function refresh() {
    setServices(await listServices());
  }

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <section>
      <h2>Services</h2>
      {services.map((service) => (
        <article key={service.id}>
          <h3>{service.name}</h3>
          <p>Status: {service.status}</p>
          <button type="button" onClick={() => startService(service.id).then(refresh)}>Start</button>
          <button type="button" onClick={() => stopService(service.id).then(refresh)}>Stop</button>
        </article>
      ))}
    </section>
  );
}
