import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';

export function HostOverviewPage() {
  const [ready, setReady] = useState<string>('unknown');

  useEffect(() => {
    apiClient
      .request<{ status: string }>('/readyz')
      .then((response) => setReady(response.status))
      .catch(() => setReady('unavailable'));
  }, []);

  return (
    <section>
      <h2>Host Overview</h2>
      <p>Agent readiness: {ready}</p>
    </section>
  );
}
