import type { Candle } from '../types/trading';
import { formatPrice } from '../lib/format';
import { clockTime, formatTime } from '../lib/time-format';
export function CandlestickChart({
  candles,
  label = 'Biểu đồ nến BTC USDT 15 phút, dữ liệu tổng hợp',
}: {
  candles: Candle[];
  label?: string;
}) {
  const visibleCandles = candles.slice(-64);
  const maxVolume = Math.max(1, ...visibleCandles.map((candle) => candle.volume));
  const low = Math.min(...visibleCandles.map((candle) => candle.low)) - 200;
  const high = Math.max(...visibleCandles.map((candle) => candle.high)) + 200;
  const priceToY = (price: number) => 25 + ((high - price) / (high - low)) * 240;
  const candleSpacing = 720 / visibleCandles.length;
  return (
    <svg viewBox="0 0 840 350" role="img" aria-label={label} className="candle-chart">
      {[0, 1, 2, 3, 4].map((index) => (
        <g key={index}>
          <line
            x1="10"
            x2="748"
            y1={25 + index * 60}
            y2={25 + index * 60}
            stroke="#e7e9e2"
            strokeDasharray="3 5"
          />
          <text x="765" y={29 + index * 60} className="axis">
            {formatPrice(high - ((high - low) * index) / 4)}
          </text>
        </g>
      ))}
      {visibleCandles.map((candle, index) => {
        const x = 16 + index * candleSpacing;
        const color = candle.close >= candle.open ? '#2c856c' : '#bd7164';
        return (
          <g key={candle.time}>
            <title>
              {formatTime(candle.time)} · O {formatPrice(candle.open)} H {formatPrice(candle.high)}{' '}
              L {formatPrice(candle.low)} C {formatPrice(candle.close)}
            </title>
            <line
              x1={x}
              x2={x}
              y1={priceToY(candle.high)}
              y2={priceToY(candle.low)}
              stroke={color}
            />
            <rect
              x={x - candleSpacing * 0.29}
              y={Math.min(priceToY(candle.open), priceToY(candle.close))}
              width={candleSpacing * 0.58}
              height={Math.max(2, Math.abs(priceToY(candle.open) - priceToY(candle.close)))}
              fill={color}
            />
            <rect
              x={x - candleSpacing * 0.29}
              y={318 - (candle.volume / maxVolume) * 45}
              width={candleSpacing * 0.58}
              height={(candle.volume / maxVolume) * 45}
              fill={color}
              opacity=".22"
            />
          </g>
        );
      })}
      <line
        x1="10"
        x2="748"
        y1={priceToY(visibleCandles.at(-1)!.close)}
        y2={priceToY(visibleCandles.at(-1)!.close)}
        stroke="#2c856c"
        strokeDasharray="5 4"
      />
      {[0, 1, 2, 3, 4].map((index) => (
        <text key={index} x={15 + index * 175} y="342" className="axis">
          {clockTime(
            visibleCandles[
              Math.min(
                visibleCandles.length - 1,
                Math.floor((index * (visibleCandles.length - 1)) / 4),
              )
            ].time,
            false,
          )}
        </text>
      ))}
    </svg>
  );
}
