import { useEffect, useState, type FormEvent, type ReactNode } from 'react';

export function AuthGate({ children }: { children: ReactNode }) {
  const [ok, setOk] = useState<boolean | null>(null);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    fetch('/api/auth/status')
      .then((r) => r.json())
      .then((s: { required: boolean; authenticated: boolean }) =>
        setOk(!s.required || s.authenticated),
      )
      .catch(() => setOk(true)); // API down: let app show its own connection errors
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    if (response.ok) setOk(true);
    else setError('Sai mật khẩu');
  }
  if (ok === null) return null;
  if (ok) return <>{children}</>;
  return (
    <form
      onSubmit={submit}
      style={{ maxWidth: 320, margin: '20vh auto', display: 'grid', gap: 12 }}
    >
      <h2>Signal Desk</h2>
      <input
        type="password"
        autoFocus
        aria-label="Mật khẩu"
        placeholder="Mật khẩu"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {error && <p role="alert">{error}</p>}
      <button type="submit">Vào</button>
    </form>
  );
}
