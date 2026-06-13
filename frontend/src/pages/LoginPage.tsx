import { FormEvent, useState } from 'react';
import { login } from '../api/auth';
import { saveSessionToken } from '../auth/session';

export type LoginPageProps = {
  onAuthenticated: (actor: string) => void;
};

export function LoginPage({ onAuthenticated }: LoginPageProps) {
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const response = await login(token);
      saveSessionToken(token);
      onAuthenticated(response.actor);
    } catch {
      setError('Authentication failed. Check the generated local bearer token.');
    }
  }

  return (
    <main className="login-page">
      <h1>IC Design Environment Guard</h1>
      <form onSubmit={submit}>
        <label htmlFor="token">Generated local bearer token</label>
        <input
          id="token"
          name="token"
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          autoComplete="current-password"
          required
        />
        <button type="submit">Sign in</button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
    </main>
  );
}
