import { getMetricsInfo } from '../api/metrics';

export function MetricsPage() {
  const info = getMetricsInfo();
  return (
    <section>
      <h2>Metrics</h2>
      <p>Scrape endpoint: <code>{info.endpoint}</code></p>
      <p>{info.accessModel}</p>
      <p>Use Prometheus or Grafana for long-term dashboards and alerting.</p>
    </section>
  );
}
