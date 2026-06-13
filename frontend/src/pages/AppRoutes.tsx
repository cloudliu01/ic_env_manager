import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { loadSessionToken } from '../auth/session';
import { LoginPage } from './LoginPage';

export function AppRoutes() {
  const [actor, setActor] = useState<string | null>(null);

  useEffect(() => {
    const token = loadSessionToken();
    if (token) {
      apiClient.setToken(token);
      setActor('local-admin');
    }
  }, []);

  if (!actor) {
    return <LoginPage onAuthenticated={setActor} />;
  }

  return (
    <main>
      <h1>IC Design Environment Guard</h1>
      <p>Signed in as {actor}</p>
      <nav aria-label="Primary">
        <a href="#terminal">Terminal</a>
        <a href="#services">Services</a>
        <a href="#metrics">Metrics</a>
        <a href="#audit">Audit</a>
      </nav>
    </main>
  );
}
