import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  LineSeries,
  ColorType,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
  type SeriesMarker,
} from 'lightweight-charts';
import type { Candle, MtfObservationSnapshot } from '../types/trading';
import { formatPrice, observationStatusLabels } from '../lib/format';

export function MtfObservationChart({
  candles,
  snapshot,
}: {
  candles: Candle[];
  snapshot: MtfObservationSnapshot | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ranges = useRef<ISeriesApi<'Line'>[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);

  useEffect(() => {
    if (!container.current) return;
    chart.current = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#fcfdf9' },
        textColor: '#63746a',
        fontFamily: "'DM Sans', sans-serif",
        fontSize: 13,
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
      ranges.current = [];
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
    if (!priceSeries || !chart.current) return;
    lines.current.forEach((line) => priceSeries.removePriceLine(line));
    lines.current = [];
    ranges.current.forEach((range) => chart.current?.removeSeries(range));
    ranges.current = [];
    markers.current?.setMarkers([]);

    if (!snapshot) return;
    const start = Math.max(candles[0]?.time ?? snapshot.range_start, snapshot.range_start);
    const end = Math.min(candles.at(-1)?.time ?? snapshot.range_end, snapshot.range_end);
    if (end > start) {
      for (const [val, color] of [
        [snapshot.range_high, '#b59854'],
        [snapshot.range_low, '#b59854'],
      ] as const) {
        const range = chart.current.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        range.setData([
          { time: start as UTCTimestamp, value: val },
          { time: end as UTCTimestamp, value: val },
        ]);
        ranges.current.push(range);
      }
    }

    if (snapshot.level !== null) {
      lines.current.push(
        priceSeries.createPriceLine({
          price: snapshot.level,
          color: '#b59854',
          lineWidth: 1,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: `VÙNG PHÁ (${snapshot.side?.toUpperCase()})`,
        }),
      );
    }
    if (snapshot.stop !== null) {
      lines.current.push(
        priceSeries.createPriceLine({
          price: snapshot.stop,
          color: '#bd7164',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'SL THAM CHIẾU',
        }),
      );
    }
    if (snapshot.target !== null) {
      lines.current.push(
        priceSeries.createPriceLine({
          price: snapshot.target,
          color: '#2c856c',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'TP THAM CHIẾU',
        }),
      );
    }

    const points: SeriesMarker<Time>[] = [];
    const map: [number | null, string, string, 'arrowUp' | 'arrowDown' | 'circle'][] = [
      [
        snapshot.breakout_time === null ? null : snapshot.breakout_time + 2700,
        'BREAKOUT 1H',
        '#b59854',
        'circle',
      ],
      [snapshot.touch_time, 'RETEST 15M', '#557fa1', 'circle'],
      [
        snapshot.confirm_time,
        'XÁC NHẬN 15M',
        '#2c856c',
        snapshot.side === 'long' ? 'arrowUp' : 'arrowDown',
      ],
    ];
    for (const [timestamp, label, color, shape] of map) {
      if (timestamp === null) continue;
      const bucket = Math.floor(timestamp / 900) * 900;
      if (candles.some((c) => c.time === bucket)) {
        points.push({
          time: bucket as UTCTimestamp,
          position: snapshot.side === 'short' ? 'aboveBar' : 'belowBar',
          color,
          shape,
          text: label,
        });
      }
    }
    markers.current?.setMarkers(points.sort((a, b) => Number(a.time) - Number(b.time)));
  }, [snapshot, candles]);

  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc">
        BTC_USDT perpetual · 15m (vùng tham chiếu từ 1h) · Chỉ quan sát, không gửi lệnh
      </div>
      <div
        ref={container}
        className="trade-candlestick-chart"
        role="img"
        aria-label="Chart quan sát đa khung"
      />
      <div className="chart-legend">
        {snapshot && (
          <strong>
            Xu hướng 4h: {snapshot.trend ? (snapshot.trend === 'up' ? 'Tăng' : 'Giảm') : '—'} ·{' '}
            {observationStatusLabels[snapshot.status] ?? snapshot.status} · Vùng{' '}
            {snapshot.level ? formatPrice(snapshot.level) : '—'}
          </strong>
        )}
        <span className="breakout-legend">— Vùng 1h tham chiếu</span>
        {snapshot && snapshot.level !== null && (
          <span className="entry-legend">— Mức breakout</span>
        )}
        {snapshot && snapshot.stop !== null && <span className="stop-legend">— SL tham chiếu</span>}
        {snapshot && snapshot.target !== null && (
          <span className="target-legend">— TP tham chiếu</span>
        )}
      </div>
    </div>
  );
}
