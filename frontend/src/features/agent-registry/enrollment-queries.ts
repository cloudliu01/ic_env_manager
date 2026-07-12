import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EnrollmentJob, getEnrollment } from './enrollment-api';

export const enrollmentKeys = {
  detail: (enrollmentId: string) => ['agent-enrollment', enrollmentId] as const,
};

const terminalStates = new Set(['verified', 'failed', 'cancelled', 'expired', 'consumed']);

function useDocumentVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  return visible;
}

export function useEnrollmentJob(enrollmentId?: string) {
  const visible = useDocumentVisible();
  return useQuery({
    queryKey: enrollmentKeys.detail(enrollmentId ?? ''),
    queryFn: ({ signal }) => getEnrollment(enrollmentId!, signal),
    enabled: Boolean(enrollmentId),
    refetchInterval: (query) => visible && query.state.data && !terminalStates.has((query.state.data as EnrollmentJob).state) ? 2_000 : false,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

export function isTerminalEnrollment(state?: string) {
  return Boolean(state && terminalStates.has(state));
}
