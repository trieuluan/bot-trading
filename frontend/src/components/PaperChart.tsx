import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type AutoscaleInfo,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
  type SeriesMarker,
} from 'lightweight-charts';
import type { Candle } from '../types/trading';
import type { PaperTrade } from '../types/paper';

export function PaperChart({ candles, trade }: { candles: Candle[]; trade: PaperTrade | null }) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);
  useEffect(() => {
    if (!container.current) return;
    chart.current = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#fcfdf9' },
        textColor: '#63746a',
        attributionLogo: true,
      },
      grid: { vertLines: { color: '#eef1e9' }, horzLines: { color: '#eef1e9' } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 8 },
      rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
    });
    series.current = chart.current.addSeries(CandlestickSeries, {
      upColor: '#2c856c',
      downColor: '#bd7164',
      wickUpColor: '#2c856c',
      wickDownColor: '#bd7164',
      borderVisible: false,
    });
    markers.current = createSeriesMarkers(series.current, []);
    return () => {
      chart.current?.remove();
      chart.current = null;
      series.current = null;
      markers.current = null;
      lines.current = [];
      fitted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!series.current || !candles.length) return;
    series.current.setData(
      candles.map((candle) => ({ ...candle, time: candle.time as UTCTimestamp })),
    );
    if (!fitted.current) {
      chart.current?.timeScale().fitContent();
      fitted.current = true;
    }
  }, [candles]);
  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries) return;
    lines.current.forEach((line) => priceSeries.removePriceLine(line));
    lines.current = [];
    const points: SeriesMarker<Time>[] = [];
    if (trade) {
      const levels = [
        { title: `#${trade.id} ENTRY`, price: trade.entry, color: '#557fa1' },
        { title: 'SL', price: trade.stop, color: '#bd7164' },
        { title: 'TP', price: trade.target, color: '#2c856c' },
        ...(trade.exit === null ? [] : [{ title: 'EXIT', price: trade.exit, color: '#8a7ca3' }]),
      ];
      lines.current = levels.map((level) =>
        priceSeries.createPriceLine({
          ...level,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
        }),
      );
      priceSeries.applyOptions({
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
          const info = original();
          if (!info?.priceRange) return info;
          return {
            ...info,
            priceRange: {
              minValue: Math.min(info.priceRange.minValue, ...levels.map((level) => level.price)),
              maxValue: Math.max(info.priceRange.maxValue, ...levels.map((level) => level.price)),
            },
          };
        },
      });
      for (const [timestamp, label] of [
        [trade.entry_time, trade.side.toUpperCase()],
        [trade.exit_time, 'EXIT'],
      ] as const) {
        if (timestamp === null) continue;
        const bucket = Math.floor(timestamp / 900) * 900;
        if (candles.some((candle) => candle.time === bucket))
          points.push({
            time: bucket as UTCTimestamp,
            position: label === 'EXIT' || trade.side === 'short' ? 'aboveBar' : 'belowBar',
            color: label === 'EXIT' ? '#8a7ca3' : '#2c856c',
            shape: label === 'EXIT' ? 'circle' : trade.side === 'long' ? 'arrowUp' : 'arrowDown',
            text: `#${trade.id} ${label}`,
          });
      }
    }
    if (!trade) priceSeries.applyOptions({ autoscaleInfoProvider: undefined });
    markers.current?.setMarkers(points);
  }, [trade, candles]);
  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc">
        BTC_USDT perpetual · 15m · UTC · kéo để di chuyển, cuộn để zoom
      </div>
      <div
        ref={container}
        className="trade-candlestick-chart"
        role="img"
        aria-label="Chart MEXC realtime và mức entry SL TP của lệnh mô phỏng"
      />
      <div className="chart-legend">
        {trade && (
          <strong>
            #{trade.id} {trade.side.toUpperCase()} ·{' '}
            {trade.exit_time === null ? 'Đang mở' : 'Đã đóng'} ·{' '}
            {trade.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}
          </strong>
        )}
        <span className="entry-legend">— Entry</span>
        <span className="stop-legend">— SL</span>
        <span className="target-legend">— TP</span>
        <span>Nến hiện tại chỉ gồm các giá đã quan sát</span>
      </div>
    </div>
  );
}
