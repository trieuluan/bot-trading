import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { BacktestTrade, Candle } from '../types/trading';
import { formatPrice } from '../lib/format';

interface TradeCandlestickChartProps {
  candles: Candle[];
  trades: BacktestTrade[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  focusRevision: number;
}

export function TradeCandlestickChart({
  candles,
  trades,
  selectedIndex,
  onSelect,
  focusRevision,
}: TradeCandlestickChartProps) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const [hover, setHover] = useState<Candle | null>(null);
  const selected = trades[selectedIndex];

  useEffect(() => {
    if (!container.current || !candles.length) return;
    const chart = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#fcfdf9' },
        textColor: '#63746a',
        fontFamily: 'Inter, sans-serif',
        attributionLogo: true,
      },
      grid: { vertLines: { color: '#eef1e9' }, horzLines: { color: '#eef1e9' } },
      rightPriceScale: { borderColor: '#dce1d7', scaleMargins: { top: 0.12, bottom: 0.24 } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#dce1d7' },
      localization: { locale: 'vi-VN' },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#2c856c',
      downColor: '#bd7164',
      wickUpColor: '#2c856c',
      wickDownColor: '#bd7164',
      borderVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    series.setData(candles.map((candle) => ({ ...candle, time: candle.time as UTCTimestamp })));
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    volume.setData(
      candles.map((candle) => ({
        time: candle.time as UTCTimestamp,
        value: candle.volume,
        color: candle.close >= candle.open ? '#2c856c40' : '#bd716440',
      })),
    );
    markersRef.current = createSeriesMarkers(series, []);
    const candlesByTime = new Map(candles.map((candle) => [candle.time, candle]));
    chart.subscribeCrosshairMove((event) => {
      setHover(typeof event.time === 'number' ? (candlesByTime.get(event.time) ?? null) : null);
    });
    chart.subscribeClick((event) => {
      if (typeof event.hoveredObjectId === 'string' && event.hoveredObjectId.startsWith('trade-')) {
        const index = Number(event.hoveredObjectId.slice(6));
        if (Number.isInteger(index) && index >= 0 && index < trades.length) onSelect(index);
      }
    });
    chartRef.current = chart;
    seriesRef.current = series;
    linesRef.current = [];
    return () => {
      chart.remove();
      markersRef.current = null;
      chartRef.current = null;
      seriesRef.current = null;
      linesRef.current = [];
    };
  }, [candles, trades, onSelect]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series || !candles.length) return;
    linesRef.current.forEach((line) => series.removePriceLine(line));
    linesRef.current = [];
    setHover(null);
    const markers: SeriesMarker<Time>[] = selected
      ? [
          {
            time: selected.entry_time as UTCTimestamp,
            position: selected.side === 'long' ? 'atPriceBottom' : 'atPriceTop',
            price: selected.entry,
            color: selected.side === 'long' ? '#2c856c' : '#b77e33',
            shape: selected.side === 'long' ? 'arrowUp' : 'arrowDown',
            text: `#${selectedIndex + 1} ${selected.side.toUpperCase()}`,
            id: `trade-${selectedIndex}`,
          },
          {
            time: selected.exit_time as UTCTimestamp,
            position: selected.side === 'long' ? 'atPriceTop' : 'atPriceBottom',
            price: selected.exit,
            color: selected.net_pnl >= 0 ? '#2c856c' : '#bd7164',
            shape: 'circle',
            text: `#${selectedIndex + 1} EXIT`,
            id: `trade-${selectedIndex}`,
          },
        ]
      : [];
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    markersRef.current?.setMarkers(markers);
    if (!selected) {
      series.applyOptions({ autoscaleInfoProvider: undefined });
      chart.timeScale().fitContent();
      return;
    }
    const levels = [
      { title: 'ENTRY', price: selected.entry, color: '#557fa1' },
      { title: 'STOP LOSS', price: selected.stop, color: '#bd7164' },
      { title: 'TAKE PROFIT', price: selected.target, color: '#2c856c' },
      { title: 'EXIT', price: selected.exit, color: '#8a7ca3' },
    ];
    linesRef.current = levels.map((level) =>
      series.createPriceLine({
        ...level,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
      }),
    );
    series.applyOptions({
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
    const entryIndex = candles.findIndex((candle) => candle.time === selected.entry_time);
    const exitIndex = candles.findIndex((candle) => candle.time === selected.exit_time);
    if (entryIndex >= 0 && exitIndex >= 0) {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, entryIndex - 20),
        to: Math.max(entryIndex + 35, exitIndex + 12),
      });
    } else {
      chart.timeScale().fitContent();
    }
  }, [candles, trades, selected, selectedIndex, focusRevision]);

  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc" aria-live="off">
        {hover ? (
          <>
            <time>
              {new Date(hover.time * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC
            </time>
            <span>O {formatPrice(hover.open)}</span>
            <span>H {formatPrice(hover.high)}</span>
            <span>L {formatPrice(hover.low)}</span>
            <span>C {formatPrice(hover.close)}</span>
          </>
        ) : (
          <span>Rê chuột để xem OHLC · kéo để di chuyển · cuộn để zoom</span>
        )}
      </div>
      <div
        ref={container}
        className="trade-candlestick-chart"
        role="img"
        aria-label="Chart nến MEXC Futures với điểm vào, thoát và mức SL TP của lệnh backtest"
      />
      <div className="chart-legend">
        <span className="entry-legend">— Entry</span>
        <span className="stop-legend">— Stop-loss</span>
        <span className="target-legend">— Take-profit</span>
        <span className="exit-legend">— Exit</span>
        <span>Volume: số hợp đồng · UTC</span>
      </div>
    </div>
  );
}
