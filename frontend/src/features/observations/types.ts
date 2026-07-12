export type ObservationsTarget = { agentId: string; name: string; capabilities: string[] };
export type Observation = {
  identity_key: string; namespace: string; name: string; kind: string; value: string | number | boolean | null; unit: string | null;
  status: 'ok' | 'warning' | 'critical' | 'unknown'; message: string | null; labels: Record<string, string>; details: Record<string, unknown>;
  observed_at: string; ttl_seconds: number; received_at: string; expires_at: string; producer_id: string; updated_at: string; stale: boolean;
};
export type ObservationPage = { items: Observation[]; next_cursor: string | null };
