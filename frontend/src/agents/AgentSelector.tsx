import { useActiveAgent } from './AgentContext';

export function AgentSelector() {
  const { agents, activeAgent, activeAgentId, error, loading, setActiveAgentId } = useActiveAgent();
  const disabled = loading || !agents.some((agent) => agent.enabled);
  const status = loading ? 'loading' : activeAgent?.status ?? (error ? 'unavailable' : 'none');

  return (
    <div className="agent-control" aria-label="Agent status">
      <label className="agent-selector">
        Active agent
        <select
          aria-label="Active agent"
          value={activeAgentId ?? ''}
          disabled={disabled}
          onChange={(event) => setActiveAgentId(event.target.value)}
        >
          {activeAgentId ? null : <option value="">No enabled agents</option>}
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id} disabled={!agent.enabled}>
              {agent.name}
            </option>
          ))}
        </select>
      </label>
      <span className={`status-badge status-${status}`}>{status}</span>
    </div>
  );
}
