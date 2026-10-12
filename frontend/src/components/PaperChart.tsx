import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  LineSeries,
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
import { exitLabels } from './PaperTradeDetails';
import type { PaperTrade } from '../types/paper';

function markerLabel(label: string, trade: PaperTrade) {
  if (label === 'EXIT') return `EXIT · ${exitLabels[trade.exit_reason ?? ''] ?? 'Đã đóng'}`;
  if (label === 'TÍN HIỆU') return label;
  return `${label} ENTRY`;
}

export function PaperChart({ candles, trade }: { candles: Candle[]; trade: PaperTrade | null }) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ranges = useRef<ISeriesApi<'Line'>[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);
  const focusedTrade = useRef<number | null>(null);
  useEffect(() => {
    if (!container.current) return;
    chart.current = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#fcfdf9' },
        textColor: '#63746a',
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: '#eef1e9' },
        horzLines: { color: '#eef1e9' },
      },
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
      candles.map((candle) => ({
        ...candle,
        time: candle.time as UTCTimestamp,
      })),
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
    ranges.current.forEach((range) => chart.current?.removeSeries(range));
    ranges.current = [];
    const points: SeriesMarker<Time>[] = [];
    if (trade) {
      const levels = [
        { title: `#${trade.id} ENTRY`, price: trade.entry, color: '#557fa1' },
        { title: 'SL', price: trade.stop, color: '#bd7164' },
        { title: 'TP', price: trade.target, color: '#2c856c' },
        ...(trade.exit === null
          ? []
          : [
              {
                title: `EXIT · ${exitLabels[trade.exit_reason ?? ''] ?? 'Đã đóng'}`,
                price: trade.exit,
                color: '#8a7ca3',
              },
            ]),
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
      if (trade.audit && chart.current) {
        const audit = trade.audit;
        for (const value of [audit.range_high, audit.range_low]) {
          const range = chart.current.addSeries(LineSeries, {
            color: '#b59854',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
          });
          range.setData([
            { time: audit.range_start as UTCTimestamp, value },
            { time: audit.range_end as UTCTimestamp, value },
          ]);
          ranges.current.push(range);
        }
      }
      for (const [timestamp, label] of [
        [trade.signal_time, 'TÍN HIỆU'],
        [trade.entry_time, trade.side.toUpperCase()],
        [trade.exit_time, 'EXIT'],
      ] as const) {
        if (timestamp === null) continue;
        const bucket = Math.floor(timestamp / 900) * 900;
        if (candles.some((candle) => candle.time === bucket))
          points.push({
            time: bucket as UTCTimestamp,
            position:
              label === 'EXIT' || label === 'TÍN HIỆU' || trade.side === 'short'
                ? 'aboveBar'
                : 'belowBar',
            color: label === 'EXIT' ? '#8a7ca3' : label === 'TÍN HIỆU' ? '#b59854' : '#2c856c',
            shape:
              label === 'EXIT' || label === 'TÍN HIỆU'
                ? 'circle'
                : trade.side === 'long'
                  ? 'arrowUp'
                  : 'arrowDown',
            text: `#${trade.id} ${markerLabel(label, trade)}`,
          });
      }
    }
    if (!trade) priceSeries.applyOptions({ autoscaleInfoProvider: undefined });
    if (focusedTrade.current !== (trade?.id ?? null)) {
      chart.current?.timeScale().fitContent();
      focusedTrade.current = trade?.id ?? null;
    }
    markers.current?.setMarkers(points.sort((a, b) => Number(a.time) - Number(b.time)));
  }, [trade, candles]);
  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc">
        <button
          className="text-button"
          onClick={() => {
            const anchor =
              trade?.signal_time ?? (trade ? Math.floor(trade.entry_time / 900) * 900 : null);
            if (anchor !== null && candles.some((c) => c.time === anchor)) {
              chart.current?.timeScale().setVisibleRange({
                from: Math.max(candles[0].time, anchor - 24 * 900) as UTCTimestamp,
                to: Math.min(
                  candles[candles.length - 1].time,
                  Math.max(anchor + 6 * 900, trade?.exit_time ?? anchor),
                ) as UTCTimestamp,
              });
            } else chart.current?.timeScale().fitContent();
          }}
        >
          {trade ? 'Tập trung vào lệnh' : 'Xem toàn bộ nến'}
        </button>
        BTC_USDT perpetual · 15m · UTC · kéo để di chuyển, cuộn để zoom
      </div>
      <div
        ref={container}
        className="trade-candlestick-chart"
        role="img"
        aria-label="Chart MEXC realtime và mức entry SL TP của lệnh mô phỏng"
      />
      {trade && !candles.some((c) => c.time === Math.floor(trade.entry_time / 900) * 900) && (
        <p className="micro">
          Chưa có nến tại thời điểm vào lệnh trong dữ liệu đã lưu; không thể hiển thị marker Entry.
        </p>
      )}
      <div className="chart-legend">
        {trade && (
          <strong>
            #{trade.id} {trade.side.toUpperCase()} ·{' '}
            {trade.exit_time === null ? 'Đang mở' : 'Đã đóng'} ·{' '}
            {trade.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}
          </strong>
        )}
        {trade && (
          <>
            <span className="entry-legend">— Entry</span>
            <span className="stop-legend">— SL</span>
            <span className="target-legend">— TP</span>
            {trade.exit !== null && <span className="exit-legend">— Exit · giá đóng</span>}
          </>
        )}
        {trade?.audit && (
          <span className="breakout-legend">— Vùng 20 nến trước · ● Nến tín hiệu</span>
        )}
        {trade?.exit_time !== null && trade ? (
          <span>Nến lịch sử / giá đã quan sát; giá realtime ở phía trên</span>
        ) : (
          <span>Nến hiện tại chỉ gồm các giá đã quan sát</span>
        )}
      </div>
    </div>
  );
}
