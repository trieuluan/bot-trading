import { useEffect, useState, type FormEvent, type ReactNode } from 'react';

export function AuthGate({ children }: { children: ReactNode }) {
  const [ok, setOk] = useState<boolean | null>(null);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    fetch('/api/auth/status')
      .then((r) => {
        if (!r.ok) throw new Error('API unavailable');
        return r.json();
      })
      .then((s: { required: boolean; authenticated: boolean }) =>
        setOk(!s.required || s.authenticated),
      )
      .catch(() => {
        setError('Không kết nối được API. Thử lại sau.');
        setOk(false);
      });
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (response.ok) {
        setPassword('');
        setOk(true);
      } else
        setError(
          response.status === 429
            ? 'Thử quá nhiều lần. Đợi một phút.'
            : 'Không đăng nhập được. Kiểm tra mật khẩu.',
        );
    } catch {
      setError('Không kết nối được API. Thử lại sau.');
    } finally {
      setBusy(false);
    }
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
        required
        maxLength={256}
        autoComplete="current-password"
        autoFocus
        aria-label="Mật khẩu"
        placeholder="Mật khẩu"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? 'Đang kiểm tra…' : 'Vào'}
      </button>
    </form>
  );
}
