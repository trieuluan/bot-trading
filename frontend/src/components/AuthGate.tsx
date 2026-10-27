import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Activity } from 'lucide-react';

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
    <main className="auth-screen grid min-h-dvh place-items-center px-4 py-10">
      <form className="auth-card min-w-0 rounded-lg border border-border-base bg-surface-1 w-full max-w-sm space-y-4 p-6 [&_input]:w-full [&_label]:block" onSubmit={submit}>
        <div className="auth-brand mb-6 flex items-center gap-2 font-display text-2xl text-bull">
          <Activity size={24} aria-hidden="true" />
          <span>
            signal<span className="brand-light font-normal text-text-muted">desk</span>
          </span>
        </div>
        <h1>Đăng nhập</h1>
        <p className="auth-sub text-xs leading-relaxed text-text-muted">Workspace giao dịch nội bộ · nhập mật khẩu ứng dụng.</p>
        <label htmlFor="auth-password">Mật khẩu</label>
        <input
          id="auth-password"
          type="password"
          required
          maxLength={256}
          autoComplete="current-password"
          autoFocus
          value={password}
          aria-invalid={!!error || undefined}
          aria-describedby={error ? 'auth-error' : undefined}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && (
          <p id="auth-error" role="alert" className="auth-error rounded border border-bear/30 bg-bear/10 p-3 text-xs text-bear">
            {error}
          </p>
        )}
        <button type="submit" className="primary inline-flex items-center justify-center gap-2 rounded-md bg-bull px-3 py-2 text-xs font-semibold text-canvas hover:bg-emerald-300" disabled={busy}>
          {busy ? 'Đang kiểm tra…' : 'Vào workspace'}
        </button>
      </form>
    </main>
  );
}
