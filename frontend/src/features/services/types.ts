export type ServiceTarget = { agentId: string; name: string; capabilities: string[] };

export type ServiceSummary = {
  id: string;
  name: string;
  status: string;
  health_status: string;
  allowed_operations: string[];
};
