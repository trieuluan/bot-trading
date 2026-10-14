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
import type { BrokerPosition } from '../types/exness-execution';
import { formatPrice, observationStatusLabels } from '../lib/format';

export type ObservationChartSnapshot = Omit<MtfObservationSnapshot, 'status' | 'trend'> & {
  status: string;
  trend: string | null;
};

export function MtfObservationChart({
  candles,
  snapshot,
  livePrice = null,
  liveCandles = [],
  executions = [],
  selectedPositionId,
  frameSeconds = 900,
  onLoadOlder,
  trendLabel = 'Xu hướng 4h',
  levelLabel = 'Mức breakout',
  title = 'BTC_USDT perpetual · 15m (vùng tham chiếu từ 1h) · Chỉ quan sát, không gửi lệnh',
}: {
  candles: Pick<Candle, 'time' | 'open' | 'high' | 'low' | 'close'>[];
  snapshot: ObservationChartSnapshot | null;
  title?: string;
  livePrice?: { price: number; label: string } | null;
  liveCandles?: Pick<Candle, 'time' | 'open' | 'high' | 'low' | 'close'>[];
  executions?: BrokerPosition[];
  selectedPositionId?: string;
  frameSeconds?: number;
  onLoadOlder?: () => void;
  trendLabel?: string;
  levelLabel?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ranges = useRef<ISeriesApi<'Line'>[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);
  const loadOlder = useRef(onLoadOlder);
  loadOlder.current = onLoadOlder;
  const previousTimes = useRef<number[]>([]);

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
    chart.current.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && range.from < 30) loadOlder.current?.();
    });
    return () => {
      chart.current?.remove();
      chart.current = null;
      series.current = null;
      markers.current = null;
      lines.current = [];
      ranges.current = [];
      fitted.current = false;
      previousTimes.current = [];
    };
  }, []);

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries || !livePrice) return;
    const line = priceSeries.createPriceLine({
      price: livePrice.price,
      color: '#287fa3',
      lineWidth: 2,
      lineStyle: LineStyle.Dotted,
      axisLabelVisible: true,
      title: livePrice.label,
    });
    return () => {
      if (series.current === priceSeries) priceSeries.removePriceLine(line);
    };
  }, [livePrice]);

  useEffect(() => {
    if (!series.current) return;
    const visible = chart.current?.timeScale().getVisibleLogicalRange();
    const oldFirst = previousTimes.current[0];
    const added = oldFirst === undefined ? 0 : candles.findIndex((bar) => bar.time === oldFirst);
    series.current.setData(
      [
        ...candles,
        ...liveCandles.filter((candle) => candle.time > (candles.at(-1)?.time ?? -1)),
      ].map((candle) => ({ ...candle, time: candle.time as UTCTimestamp })),
    );
    previousTimes.current = candles.map((bar) => bar.time);
    if (visible && added > 0) {
      chart.current?.timeScale().setVisibleLogicalRange({
        from: visible.from + added,
        to: visible.to + added,
      });
    }
    if (!fitted.current && candles.length) {
      chart.current?.timeScale().fitContent();
      fitted.current = true;
    }
  }, [candles, liveCandles]);

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries || !chart.current) return;
    lines.current.forEach((line) => priceSeries.removePriceLine(line));
    lines.current = [];
    ranges.current.forEach((range) => chart.current?.removeSeries(range));
    ranges.current = [];
    markers.current?.setMarkers([]);

    const points: SeriesMarker<Time>[] = [];
    if (snapshot) {
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
            title:
              levelLabel === 'Mức breakout'
                ? `VÙNG PHÁ (${snapshot.side?.toUpperCase() ?? 'CHỜ'})`
                : levelLabel,
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

      const map: [number | null, string, string, 'arrowUp' | 'arrowDown' | 'circle'][] = [
        [
          snapshot.breakout_time === null ? null : snapshot.breakout_time + 2700,
          'BREAKOUT 1H',
          '#b59854',
          'circle',
        ],
        [snapshot.touch_time, 'RETEST 15M', '#557fa1', 'circle'],
        [
          ['cancelled', 'expired'].includes(snapshot.status) ? snapshot.candle_time : null,
          snapshot.status === 'expired' ? 'HẾT HẠN' : 'HỦY SETUP',
          '#bd7164',
          'circle',
        ],
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
    }
    for (const trade of executions) {
      for (const deal of trade.deals) {
        if (!['open', 'close'].includes(deal.type)) continue;
        const bucket = Math.floor(deal.event_time / frameSeconds) * frameSeconds;
        if (!candles.some((candle) => candle.time === bucket)) continue;
        const entry = deal.type === 'open';
        points.push({
          time: bucket as UTCTimestamp,
          position: entry
            ? trade.side === 'buy'
              ? 'belowBar'
              : 'aboveBar'
            : trade.side === 'buy'
              ? 'aboveBar'
              : 'belowBar',
          color: entry
            ? '#557fa1'
            : trade.pnl == null
              ? '#63746a'
              : trade.pnl >= 0
                ? '#2c856c'
                : '#bd7164',
          shape: entry ? (trade.side === 'buy' ? 'arrowUp' : 'arrowDown') : 'circle',
          text: `${entry ? trade.side?.toUpperCase() : 'EXIT'} #${trade.position_id} @ ${deal.price}`,
        });
      }
      if (trade.position_id === selectedPositionId) {
        for (const [price, color, title] of [
          [trade.open_price, '#557fa1', 'ENTRY KHỚP SÀN'],
          [trade.stop, '#bd7164', 'SL SÀN'],
          [trade.target, '#2c856c', 'TP SÀN'],
          [trade.close_price, '#63746a', 'EXIT KHỚP SÀN'],
        ] as const) {
          if (price == null || price <= 0) continue;
          lines.current.push(
            priceSeries.createPriceLine({
              price,
              color,
              title,
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
            }),
          );
        }
      }
    }
    markers.current?.setMarkers(points.sort((a, b) => Number(a.time) - Number(b.time)));
  }, [snapshot, candles, levelLabel, executions, selectedPositionId, frameSeconds]);

  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc">{title}</div>
      <div
        ref={container}
        className="trade-candlestick-chart"
        role="img"
        aria-label="Chart quan sát đa khung"
      />
      <div className="chart-legend">
        {snapshot && (
          <strong>
            {trendLabel}:{' '}
            {snapshot.trend === 'up'
              ? 'Tăng'
              : snapshot.trend === 'down'
                ? 'Giảm'
                : snapshot.trend === 'sideway'
                  ? 'Đi ngang'
                  : 'Chưa rõ'}{' '}
            · {observationStatusLabels[snapshot.status] ?? snapshot.status} · Vùng{' '}
            {snapshot.level ? formatPrice(snapshot.level) : '—'}
          </strong>
        )}
        <span className="breakout-legend">— Vùng 1h tham chiếu</span>
        {snapshot && snapshot.level !== null && (
          <span className="entry-legend">— {levelLabel}</span>
        )}
        {snapshot && snapshot.stop !== null && <span className="stop-legend">— SL tham chiếu</span>}
        {snapshot && snapshot.target !== null && (
          <span className="target-legend">— TP tham chiếu</span>
        )}
      </div>
    </div>
  );
}
