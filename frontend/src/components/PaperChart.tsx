import { getFuturesSymbol } from '../lib/futures-symbol';
import { useEffect, useMemo, useRef, useState } from 'react';
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
import { placeChartTags } from '../lib/chart-tags';
import { formatPrice } from '../lib/format';
import { estimatedPaperPnl } from '../lib/paper-position';
import type { FuturesStrategyObservation, PaperTrade } from '../types/paper';

import { aggregateCandles, projectOverlayPoints, recentChartRange, RECENT_CHART_BARS, CHART_RIGHT_SPACE, TIMEFRAMES, type ChartTimeframe } from '../lib/chart-timeframes';

const TIMEFRAME_KEY = 'futures-chart-timeframe';

export function strategyColor(strategy: string) {
  const families = [
    'breakout',
    'mtf_retest',
    'mean_reversion',
    'liquidity_sweep',
    'funding_filter',
    'retest',
    'squeeze',
    'trend_pullback',
  ];
  return ['#327d9c', '#ad7730', '#a26991', '#65823d', '#907740', '#5b78b7', '#bb6a4c', '#298b80'][
    Math.max(0, families.indexOf(strategy.split(':')[0]))
  ];
}

function markerLabel(label: string, trade: PaperTrade) {
  if (label === 'EXIT') return `EXIT · ${exitLabels[trade.exit_reason ?? ''] ?? 'Đã đóng'}`;
  if (label === 'TÍN HIỆU') return label;
  return `${label} ENTRY`;
}

export function PaperChart({
  candles: baseCandles,
  trade,
  observations = [],
  positions = [],
  onSelectPosition,
  markPrice = null,
  feeBps = 0,
  markStale = false,
  onLoadOlder,
}: {
  candles: Candle[];
  trade: PaperTrade | null;
  positions?: PaperTrade[];
  onSelectPosition?: (id: number) => void;
  observations?: (FuturesStrategyObservation & { time: number })[];
  markPrice?: number | null;
  feeBps?: number;
  markStale?: boolean;
  onLoadOlder?: (m15Bars: number) => void;
}) {
  const [timeframe, setTimeframe] = useState<ChartTimeframe>(() => {
    try {
      const saved = localStorage.getItem(TIMEFRAME_KEY);
      return saved && Object.hasOwn(TIMEFRAMES, saved) ? (saved as ChartTimeframe) : '15m';
    } catch {
      return '15m';
    }
  });
  const step = TIMEFRAMES[timeframe];
  const candles = useMemo(() => aggregateCandles(baseCandles, step), [baseCandles, step]);
  const stepRef = useRef(900);
  const loadOlder = useRef(onLoadOlder);
  loadOlder.current = onLoadOlder;
  stepRef.current = step;
  const oldestTime = baseCandles[0]?.time;
  useEffect(() => {
    if (oldestTime !== undefined && step > 900 && candles.length < RECENT_CHART_BARS) {
      loadOlder.current?.(step / 900);
    }
  }, [step, oldestTime, candles.length]);
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ranges = useRef<ISeriesApi<'Line'>[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);
  const previousCandles = useRef<Candle[]>([]);
  function showRecentCandles() {
    if (!candles.length) return;
    chart.current?.timeScale().setVisibleLogicalRange(recentChartRange(candles.length));
  }
  const snap = (time: number) => Math.floor(time / step) * step;
  const [hover, setHover] = useState<string | null>(null);
  const [tags, setTags] = useState<
    { id: number; kind: 'entry' | 'stop' | 'target'; y: number; displayY: number; column: number }[]
  >([]);
  const openTrades = positions.length
    ? positions
    : trade && trade.exit_time === null
      ? [trade]
      : [];
  useEffect(() => {
    if (!openTrades.length) {
      setTags([]);
      return;
    }
    let frame = 0;
    let last = '';
    const tick = () => {
      const priceSeries = series.current;
      if (priceSeries) {
        const next = openTrades.flatMap((open) =>
          (
            [
              ['entry', open.entry],
              ['stop', open.stop],
              ['target', open.target],
            ] as const
          ).flatMap(([kind, price]) => {
            const y = priceSeries.priceToCoordinate(price);
            return y === null ? [] : [{ id: open.id, kind, y: Math.round(y) }];
          }),
        );
        const placed = placeChartTags(
          next,
          Math.max(0, (container.current?.clientHeight ?? 0) - 30),
        );
        const key = placed
          .map((tag) => `${tag.id}:${tag.kind}:${tag.displayY}:${tag.column}`)
          .join('|');
        if (key !== last) {
          last = key;
          setTags(placed);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [positions, trade]);
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
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: CHART_RIGHT_SPACE },
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
      if (range && range.from < 30) loadOlder.current?.(stepRef.current / 900);
    });
    return () => {
      chart.current?.remove();
      chart.current = null;
      series.current = null;
      markers.current = null;
      lines.current = [];
      ranges.current = [];
      fitted.current = false;
      previousCandles.current = [];
    };
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(TIMEFRAME_KEY, timeframe);
    } catch {}
    markers.current?.setMarkers([]);
    fitted.current = false;
    previousCandles.current = [];
  }, [timeframe]);
  useEffect(() => {
    if (!series.current) return;
    const previous = previousCandles.current;
    const sameHistory =
      previous.length > 0 &&
      (candles.length === previous.length || candles.length === previous.length + 1) &&
      candles.slice(0, -1).every((candle, index) => {
        const old = previous[index];
        return (
          old &&
          Object.keys(candle).every(
            (key) => candle[key as keyof Candle] === old[key as keyof Candle],
          )
        );
      }) &&
      (candles.at(-1)?.time ?? 0) >= (previous.at(-1)?.time ?? 0);
    if (sameHistory) {
      const last = candles.at(-1)!;
      series.current.update({ ...last, time: last.time as UTCTimestamp });
    } else {
      // Keep the visible window when older bars are prepended.
      const scale = chart.current?.timeScale();
      const range = scale?.getVisibleLogicalRange();
      const prepended =
        previous.length > 0 && candles.length > 0 && candles[0].time < previous[0].time
          ? candles.findIndex((candle) => candle.time === previous[0].time)
          : 0;
      series.current.setData(
        candles.map((candle) => ({ ...candle, time: candle.time as UTCTimestamp })),
      );
      if (range && prepended > 0)
        scale?.setVisibleLogicalRange({ from: range.from + prepended, to: range.to + prepended });
    }
    previousCandles.current = candles;
    if (!candles.length) return;
    const replacedHistory =
      previous.length > 0 &&
      (candles.at(-1)!.time < previous[0].time || candles[0].time > previous.at(-1)!.time);
    if (!fitted.current || replacedHistory) {
      showRecentCandles();
      // Refresh may deliver only the live bar first; keep refitting until real history arrives.
      fitted.current = candles.length >= RECENT_CHART_BARS;
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
    const allLevels: { title: string; price: number; color: string; lineStyle?: LineStyle }[] = [];
    for (const position of positions) {
      if (position.id !== trade?.id) {
        const label = `#${position.id} ${position.side.toUpperCase()}`;
        allLevels.push(
          { title: `${label} ENTRY`, price: position.entry, color: '#557fa1' },
          { title: `${label} SL`, price: position.stop, color: '#bd7164' },
          { title: `${label} TP`, price: position.target, color: '#2c856c' },
        );
        const entryTime = Math.floor(position.entry_time / step) * step;
        if (candles.some((candle) => candle.time === entryTime)) {
          points.push({
            time: entryTime as UTCTimestamp,
            position: position.side === 'long' ? 'belowBar' : 'aboveBar',
            color: position.side === 'long' ? '#2c856c' : '#bd7164',
            shape: position.side === 'long' ? 'arrowUp' : 'arrowDown',
            text: `${label} ENTRY`,
          });
        }
      }
    }
    if (trade) {
      for (const leg of trade.monitoring?.partial_exits ?? []) {
        const bucket = Math.floor(leg.time / step) * step;
        if (candles.some((candle) => candle.time === bucket)) {
          points.push({
            time: bucket as UTCTimestamp,
            position: trade.side === 'long' ? 'aboveBar' : 'belowBar',
            color: '#2c856c', shape: 'circle',
            text: `#${trade.id} CHỐT MỘT PHẦN ${formatPrice(leg.price)}`,
          });
        }
      }
      const label = `#${trade.id} ${trade.side.toUpperCase()}`;
      allLevels.push(
        { title: `${label} ENTRY`, price: trade.entry, color: '#557fa1' },
        { title: `${label} SL`, price: trade.stop, color: '#bd7164' },
        { title: `${label} TP`, price: trade.target, color: '#2c856c' },
        ...(trade.exit === null
          ? []
          : [
              {
                title: `EXIT · ${exitLabels[trade.exit_reason ?? ''] ?? 'Đã đóng'}`,
                price: trade.exit,
                color: '#8a7ca3',
              },
            ]),
      );
      if (trade.audit?.range_start != null && trade.audit.range_end != null && chart.current) {
        const audit = trade.audit;
        for (const value of [audit.range_high, audit.range_low]) {
          if (value == null) continue;
          const range = chart.current.addSeries(LineSeries, {
            color: '#b59854',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
            pointMarkersVisible: true,
          });
          range.setData(projectOverlayPoints([
            { time: audit.range_start!, value },
            { time: audit.range_end!, value },
          ], step).map((point) => ({ ...point, time: point.time as UTCTimestamp })));
          ranges.current.push(range);
        }
      }
      for (const [timestamp, label] of [
        [trade.signal_time, 'TÍN HIỆU'],
        [trade.entry_time, trade.side.toUpperCase()],
        [trade.exit_time, 'EXIT'],
      ] as const) {
        if (timestamp === null) continue;
        const bucket = Math.floor(timestamp / step) * step;
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
    for (const observation of observations) {
      const color = strategyColor(observation.strategy);
      const title = observation.strategy.replace(':', ' · ').slice(0, 32);
      const heldByTrade = openTrades.some((open) => open.audit?.strategy === observation.strategy);
      if (observation.setup && !heldByTrade) {
        const family = observation.strategy.split(':')[0].replace(/_/g, ' ');
        allLevels.push(
          {
            title: `${family} ENTRY`,
            price: observation.setup.entry,
            color: '#557fa1',
            lineStyle: LineStyle.Dotted,
          },
          {
            title: `${family} SL`,
            price: observation.setup.stop,
            color: '#bd7164',
            lineStyle: LineStyle.Dotted,
          },
          {
            title: `${family} TP`,
            price: observation.setup.target,
            color: '#2c856c',
            lineStyle: LineStyle.Dotted,
          },
        );
      }
      for (const level of observation.levels ?? []) {
        const start = snap(
          Math.max(candles[0]?.time ?? observation.time, level.from ?? observation.time - 20 * 900),
        );
        const end = snap(Math.min(level.to ?? observation.time, observation.time));
        if (!chart.current || !Number.isFinite(level.price) || start > end) continue;
        const range = chart.current.addSeries(LineSeries, {
          title: `${title} · ${level.title}`,
          color,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          pointMarkersVisible: start === end,
          autoscaleInfoProvider: () => null,
        });
        range.setData(projectOverlayPoints([
          { time: start as UTCTimestamp, value: level.price },
          { time: end as UTCTimestamp, value: level.price },
        ], step).map((point) => ({ ...point, time: point.time as UTCTimestamp })));
        ranges.current.push(range);
      }
      for (const curve of observation.curves ?? []) {
        if (!chart.current) continue;
        const visible = projectOverlayPoints(curve.points.filter((point) =>
            point.time >= (candles[0]?.time ?? 0) &&
            point.time <= observation.time,
        ), step);
        if (!visible.length) continue;
        const indicator = chart.current.addSeries(LineSeries, {
          title: `${title} · ${curve.title}`,
          color,
          lineWidth: 2,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          pointMarkersVisible: visible.length === 1,
          autoscaleInfoProvider: () => null,
        });
        indicator.setData(
          visible.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })),
        );
        ranges.current.push(indicator);
      }
      if (
        observation.setup &&
        !heldByTrade &&
        candles.some((candle) => candle.time === Math.floor(observation.time / step) * step)
      ) {
        points.push({
          time: (Math.floor(observation.time / step) * step) as UTCTimestamp,
          position: observation.setup.side === 'long' ? 'belowBar' : 'aboveBar',
          color,
          shape: 'circle',
          text: `${title} · TÍN HIỆU ${observation.setup.side.toUpperCase()}`,
        });
      }
    }
    lines.current = allLevels.map((level) =>
      priceSeries.createPriceLine({
        title: level.title,
        price: level.price,
        color: level.color,
        lineWidth: 1,
        lineStyle: level.lineStyle ?? LineStyle.Solid,
        axisLabelVisible: true,
      }),
    );
    if (allLevels.length > 0) {
      priceSeries.applyOptions({
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
          const info = original();
          if (!info?.priceRange) return info;
          return {
            ...info,
            priceRange: {
              minValue: Math.min(
                info.priceRange.minValue,
                ...allLevels.map((level) => level.price),
              ),
              maxValue: Math.max(
                info.priceRange.maxValue,
                ...allLevels.map((level) => level.price),
              ),
            },
          };
        },
      });
    } else {
      priceSeries.applyOptions({ autoscaleInfoProvider: undefined });
    }
    markers.current?.setMarkers(points.sort((a, b) => Number(a.time) - Number(b.time)));
  }, [trade, observations, candles, positions, step]);
  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc">
        <button
          className="text-button"
          onClick={() => {
            const timestamp = trade?.signal_time ?? trade?.entry_time;
            const anchor = timestamp == null ? null : Math.floor(timestamp / step) * step;
            if (anchor !== null && candles.some((c) => c.time === anchor)) {
              chart.current?.timeScale().setVisibleRange({
                from: Math.max(candles[0].time, anchor - 24 * step) as UTCTimestamp,
                to: Math.min(
                  candles[candles.length - 1].time,
                  Math.max(anchor + 6 * step, trade?.exit_time ?? anchor),
                ) as UTCTimestamp,
              });
            } else showRecentCandles();
          }}
        >
          {trade ? 'Tập trung vào lệnh' : `${RECENT_CHART_BARS} nến gần nhất`}
        </button>
        <div className="chart-timeframes" role="group" aria-label="Khung thời gian">
          {(Object.keys(TIMEFRAMES) as ChartTimeframe[]).map((frame) => (
            <button
              key={frame}
              type="button"
              className={frame === timeframe ? 'active' : ''}
              aria-pressed={frame === timeframe}
              onClick={() => setTimeframe(frame)}
            >
              {frame}
            </button>
          ))}
        </div>
        {getFuturesSymbol()} perpetual · {timeframe} · UTC · kéo để di chuyển, cuộn để zoom
      </div>
      <div className="paper-chart-stage">
        <div
          ref={container}
          className="trade-candlestick-chart"
          role="img"
          aria-label="Chart MEXC realtime và mức entry SL TP của lệnh mô phỏng"
        />
        <div className="position-tag-layer">
          {tags.map((tag) => {
            const open = openTrades.find((position) => position.id === tag.id);
            if (!open) return null;
            const tagKey = `${tag.id}-${tag.kind}`;
            const price =
              tag.kind === 'entry' ? markPrice : tag.kind === 'stop' ? open.stop : open.target;
            const value = estimatedPaperPnl(open, price, feeBps, false);
            const net = estimatedPaperPnl(open, price, feeBps);
            const label =
              tag.kind === 'entry'
                ? 'HIỆN TẠI'
                : tag.kind === 'stop'
                  ? 'NẾU SL'
                  : 'NẾU TP';
            const sign = open.side === 'long' ? 1 : -1;
            const fmt = (n: number | null, d = 2) =>
              n === null ? '—' : `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(d)}`;
            const exitFee = price === null ? null : (price * open.quantity * feeBps) / 10000;
            const move = price === null ? null : ((price - open.entry) / open.entry) * 100 * sign;
            const left = 12 + tag.column * 300;
            return (
              <div key={tagKey}>
                <div
                  className={`position-tag ${value === null ? 'flat' : value >= 0 ? 'win' : 'loss'} ${open.id === trade?.id ? 'is-selected' : ''}`}
                  style={{ top: tag.displayY, left }}
                  onMouseEnter={() => setHover(tagKey)}
                  tabIndex={0}
                  onFocus={() => setHover(tagKey)}
                  onBlur={() => setHover(null)}
                  onClick={() => onSelectPosition?.(open.id)}
                  role="button"
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelectPosition?.(open.id);
                    }
                  }}
                  onMouseLeave={() => setHover(null)}
                >
                  <span>
                    #{open.id} {open.side.toUpperCase()} {label}
                    {tag.kind === 'entry' && markStale ? ' · GIÁ CŨ' : ''}
                  </span>
                  <strong>
                    {value === null
                      ? '—'
                      : `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)} USDT`}
                  </strong>
                </div>
                {hover === tagKey && (
                  <dl
                    className="position-tip"
                    style={
                      tag.displayY > (container.current?.clientHeight ?? 430) * 0.48
                        ? {
                            bottom: (container.current?.clientHeight ?? 430) - tag.displayY + 16,
                            left,
                          }
                        : { top: tag.displayY + 16, left }
                    }
                  >
                    <h4>
                      #{open.id} · {open.side === 'long' ? 'LONG' : 'SHORT'} ·{' '}
                      {tag.kind === 'entry'
                        ? 'Giá hiện tại'
                        : tag.kind === 'stop'
                          ? 'Nếu chạm SL'
                          : 'Nếu chạm TP'}
                    </h4>
                    <div>
                      <dt>Giá {tag.kind === 'entry' ? 'hiện tại' : 'chốt'}</dt>
                      <dd>{price === null ? '—' : formatPrice(price)}</dd>
                    </div>
                    <div>
                      <dt>Giá vào</dt>
                      <dd>{formatPrice(open.entry)}</dd>
                    </div>
                    <div>
                      <dt>SL / TP</dt>
                      <dd>
                        {formatPrice(open.stop)} / {formatPrice(open.target)}
                      </dd>
                    </div>
                    <div>
                      <dt>Khối lượng</dt>
                      <dd>{open.quantity} BTC</dd>
                    </div>
                    <div>
                      <dt>Biến động giá</dt>
                      <dd>{fmt(move)}%</dd>
                    </div>
                    <div>
                      <dt>PnL theo giá</dt>
                      <dd>{fmt(value)} USDT</dd>
                    </div>
                    <div>
                      <dt>Phí vào / ra ({feeBps} bps)</dt>
                      <dd>
                        −{open.entry_fee.toFixed(2)} / −
                        {exitFee === null ? '—' : exitFee.toFixed(2)}
                      </dd>
                    </div>
                    <div>
                      <dt>Funding đã ghi</dt>
                      <dd>{fmt(-open.funding_cost)}</dd>
                    </div>
                    <div className="total">
                      <dt>PnL ròng nếu chốt</dt>
                      <dd>{fmt(net)} USDT</dd>
                    </div>
                    <p>
                      Chưa gồm trượt giá và funding tương lai{markStale ? ' · giá đang chậm' : ''}.
                    </p>
                  </dl>
                )}
              </div>
            );
          })}
          <svg className="position-tag-leaders" aria-hidden="true">
            {tags
              .filter((tag) => tag.y !== tag.displayY)
              .map((tag) => (
                <line
                  key={`${tag.id}-${tag.kind}`}
                  x1={6}
                  y1={tag.y}
                  x2={12 + tag.column * 300}
                  y2={tag.displayY}
                />
              ))}
          </svg>
        </div>
      </div>
      {trade && !candles.some((c) => c.time === Math.floor(trade.entry_time / step) * step) && (
        <p className="micro">
          Chưa có nến tại thời điểm vào lệnh trong dữ liệu đã lưu; không thể hiển thị marker Entry.
        </p>
      )}
      <div className="chart-legend">
        {openTrades.length > 0 && (
          <span>
            PnL trên thẻ là chênh lệch giá × khối lượng, chưa trừ phí; rê chuột vào thẻ để xem số
            sau phí.
          </span>
        )}
        {trade && (
          <strong>
            #{trade.id} {trade.side.toUpperCase()} ·{' '}
            {trade.exit_time === null ? 'Đang mở' : 'Đã đóng'} ·{' '}
            {trade.origin === 'auto' ? 'AUTO' : 'THỦ CÔNG'}
            {trade.audit?.strategy ? ` · ${trade.audit.strategy}` : ''}
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
        {step !== 900 && <span>Lớp phân tích giữ dữ liệu nguồn, gộp mốc thời gian theo {timeframe}; không tính lại chỉ báo theo khung xem. Nến cuối có thể chưa đóng.</span>}
        {observations.length > 0 && (
          <span>
            {observations.length} lớp phân tích · nét đứt: vùng tham chiếu · nét liền: chỉ báo ·
            marker: tín hiệu nến đóng · ENTRY/SL/TP dự kiến không phải lệnh đã khớp
          </span>
        )}
        {trade?.audit?.range_high != null && (
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
