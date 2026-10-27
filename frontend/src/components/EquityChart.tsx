import type { BacktestPeriod } from '../types/trading';
import { formatPrice } from '../lib/format';

export function EquityChart({ points }: { points: BacktestPeriod['equity_curve'] }) {
  if (!points.length) return null;
  const low = Math.min(...points.map((point) => point.equity));
  const high = Math.max(...points.map((point) => point.equity));
  const range = Math.max(high - low, 1);
  const stride = Math.max(1, Math.floor(points.length / 600));
  const sampled = points.filter((_, i) => i % stride === 0 || i === points.length - 1);
  const coordinates = sampled.map((point) => {
    const x =
      20 +
      ((point.time - points[0].time) / Math.max(1, points.at(-1)!.time - points[0].time)) * 900;
    return `${x},${25 + ((high - point.equity) / range) * 180}`;
  });
  return (
    <svg
      viewBox="0 0 960 245"
      className="equity-chart relative min-h-48 min-w-0 [&_svg]:w-full [&_.axis]:fill-text-muted"
      role="img"
      aria-label="Đường vốn sau phí và funding"
    >
      {[0, 1, 2].map((i) => (
        <g key={i}>
          <line x1="20" x2="920" y1={25 + i * 90} y2={25 + i * 90} stroke="#e7e9e2" />
          <text x="22" y={20 + i * 90} className="axis fill-text-muted text-xs">
            {formatPrice(high - (range * i) / 2)} USDT
          </text>
        </g>
      ))}
      <polyline points={coordinates.join(' ')} fill="none" stroke="#2c856c" strokeWidth="2" />
      <text x="20" y="236" className="axis fill-text-muted text-xs">
        {new Date(points[0].time * 1000).toLocaleDateString('vi-VN', { timeZone: 'UTC' })}
      </text>
      <text x="920" y="236" textAnchor="end" className="axis fill-text-muted text-xs">
        {new Date(points.at(-1)!.time * 1000).toLocaleDateString('vi-VN', { timeZone: 'UTC' })}
      </text>
    </svg>
  );
}
