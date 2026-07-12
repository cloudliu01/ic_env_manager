import { lazy, Suspense } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useRuntime } from './RuntimeProvider';

const AgentEntry = lazy(() => import('./AgentEntry'));
const LegacyManagerApp = lazy(() => import('../pages/AppRoutes').then((module) => ({ default: module.AppRoutes })));

function ManagerEntry() {
  const location = useLocation();
  if (location.pathname !== '/fleet') {
    return <Navigate to="/fleet" replace />;
  }
  return <LegacyManagerApp />;
}

export function RuntimeRouter() {
  const runtime = useRuntime();
  return (
    <Suspense fallback={<main><p role="status">Loading application…</p></main>}>
      {runtime.mode === 'agent' ? <AgentEntry /> : <ManagerEntry />}
    </Suspense>
  );
}
