export type MetricsInfo = {
  endpoint: string;
  accessModel: string;
};

export function getMetricsInfo(): MetricsInfo {
  return {
    endpoint: '/metrics',
    accessModel: 'Local access by default; remote access requires configured network allowlist.',
  };
}
