import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { cancelDiscoveryJob, startDiscovery } from './api';
import { discoveryKeys, useDiscoveryJob, useDiscoveryResults, useDiscoveryScopes } from './queries';

const terminal = new Set(['completed', 'failed', 'cancelled']);

export function DiscoveryPage() {
  const [params, setParams] = useSearchParams();
  const client = useQueryClient();
  const [scopeId, setScopeId] = useState('');
  const [error, setError] = useState('');
  const jobId = params.get('job') ?? undefined;
  const scopes = useDiscoveryScopes();
  const job = useDiscoveryJob(jobId);
  const results = useDiscoveryResults(jobId, Boolean(jobId));
  const start = async () => { if (!scopeId) { setError('Choose one configured discovery scope.'); return; } setError(''); try { const started = await startDiscovery(scopeId); setParams({ job: started.job.job_id }); } catch { setError('Discovery could not start. Retry the configured scope.'); } };
  const cancel = async () => { if (!jobId) return; try { await cancelDiscoveryJob(jobId); await client.invalidateQueries({ queryKey: discoveryKeys.job(jobId) }); } catch { setError('Discovery could not be cancelled. Retry or wait for its current state.'); } };
  const retry = () => { setParams({}); setError(''); };
  const current = job.data?.job;
  const selectedScope = scopes.data?.scopes.find((scope) => scope.id === scopeId);
  useEffect(() => {
    if (jobId && current?.state) void client.invalidateQueries({ queryKey: discoveryKeys.results(jobId) });
  }, [client, current?.state, jobId]);
  return <section className="feature-page"><header className="page-header"><h1 tabIndex={-1}>Discovery</h1><p>Scan only Manager-configured named scopes.</p></header>
    {error ? <p role="alert">{error}</p> : null}
    <label className="form-field">Discovery scope<select aria-label="Discovery scope" value={scopeId} onChange={(event) => setScopeId(event.target.value)} disabled={Boolean(jobId)}><option value="">Choose a configured scope</option>{scopes.data?.scopes.map((scope) => <option value={scope.id} key={scope.id}>{scope.name} ({scope.target_count} targets)</option>)}</select><span className="secondary-cell">Network ranges, addresses, and ports are defined by Manager, not this form.</span></label>
    {selectedScope ? <section className="detail-panel" aria-label="Selected discovery scope"><h2>{selectedScope.name}</h2><p>CIDR {selectedScope.cidr}</p>{selectedScope.endpoints.map((endpoint) => <p key={`${endpoint.port}:${endpoint.transport_profile_id}`}>Port {endpoint.port} · Profile {endpoint.transport_profile_id}</p>)}<p>{selectedScope.target_count} bounded targets</p></section> : null}
    {!jobId ? <button type="button" onClick={() => void start()} disabled={!scopes.data?.enabled}>Start discovery</button> : null}
    {current ? <section className="detail-panel" aria-live="polite"><h2>Discovery {current.state}</h2><p>{current.checked_targets} checked of {current.total_targets}</p><p>{current.found_targets} found</p>{current.error_code ? <p role="alert">Discovery ended with {current.error_code}. Retry this configured scope.</p> : null}{!terminal.has(current.state) ? <button type="button" className="secondary-button" onClick={() => void cancel()}>Cancel discovery</button> : <button type="button" className="secondary-button" onClick={retry}>Retry discovery</button>}</section> : null}
    {results.data?.results.map((result) => <article className="detail-panel" key={result.result_id}><h2>{result.candidate_url}</h2><p>Address {result.ip} · Port {result.port} · Profile {result.transport_profile_id}</p><p>Status: {result.status}. Enrollment: {result.enrollment_status ?? 'not available'}.</p>{result.enrollment_status === 'enrollment_required' ? <Link to={`/agents/new?discoveryResult=${encodeURIComponent(result.result_id)}`}>Enroll candidate</Link> : null}</article>)}
  </section>;
}
