import { lazy, Suspense } from 'react';
import { useRuntime } from './RuntimeProvider';

const AgentEntry = lazy(() => import('./AgentEntry'));
const ManagerEntry = lazy(() => import('./ManagerEntry'));

export function RuntimeRouter() {
  const runtime = useRuntime();
  return <Suspense fallback={<main><p role="status">Loading application…</p></main>}>
    {runtime.mode === 'agent' ? <AgentEntry /> : <ManagerEntry capabilities={runtime.capabilities} />}
  </Suspense>;
}
