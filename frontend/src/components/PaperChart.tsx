import { getFuturesSymbol } from '../lib/futures-symbol';
import { useEffect, useRef, useState } from 'react';
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
  candles,
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
  onLoadOlder?: () => void;
}) {
  const loadOlder = useRef(onLoadOlder);
  loadOlder.current = onLoadOlder;
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
    chart.current?.timeScale().setVisibleRange({
      from: candles[Math.max(0, candles.length - 40)].time as UTCTimestamp,
      to: (candles.at(-1)!.time + 900) as UTCTimestamp,
    });
  }
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
            if (kind !== 'entry' && open.id !== trade?.id) return [];
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
      previousCandles.current = [];
    };
  }, []);
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
    const allLevels: { title: string; price: number; color: string; lineStyle?: LineStyle }[] = [];
    for (const position of positions) {
      if (position.id !== trade?.id) {
        allLevels.push({ title: `#${position.id} ENTRY`, price: position.entry, color: '#557fa1' });
      }
    }
    if (trade) {
      allLevels.push(
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
    for (const observation of observations) {
      const color = strategyColor(observation.strategy);
      const title = observation.strategy.replace(':', ' · ').slice(0, 32);
      const heldByTrade = trade?.exit === null && trade.audit?.strategy === observation.strategy;
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
        const start = Math.max(
          candles[0]?.time ?? observation.time,
          level.from ?? observation.time - 20 * 900,
        );
        const end = Math.min(level.to ?? observation.time, observation.time);
        if (!chart.current || !Number.isFinite(level.price) || start >= end) continue;
        const range = chart.current.addSeries(LineSeries, {
          title: `${title} · ${level.title}`,
          color,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null,
        });
        range.setData([
          { time: start as UTCTimestamp, value: level.price },
          { time: end as UTCTimestamp, value: level.price },
        ]);
        ranges.current.push(range);
      }
      for (const curve of observation.curves ?? []) {
        if (!chart.current) continue;
        const visible = curve.points.filter(
          (point) =>
            point.time >= (candles[0]?.time ?? 0) &&
            point.time <= observation.time &&
            Number.isFinite(point.value),
        );
        if (visible.length < 2) continue;
        const indicator = chart.current.addSeries(LineSeries, {
          title: `${title} · ${curve.title}`,
          color,
          lineWidth: 2,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
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
        candles.some((candle) => candle.time === observation.time)
      ) {
        points.push({
          time: observation.time as UTCTimestamp,
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
        lineStyle: level.lineStyle ?? LineStyle.Dashed,
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
  }, [trade, observations, candles, positions]);
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
            } else showRecentCandles();
          }}
        >
          {trade ? 'Tập trung vào lệnh' : '40 nến gần nhất'}
        </button>
        {getFuturesSymbol()} perpetual · 15m · UTC · kéo để di chuyển, cuộn để zoom
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
                ? `#${open.id} HIỆN TẠI`
                : tag.kind === 'stop'
                  ? 'CHẠM SL'
                  : 'CHẠM TP';
            const sign = open.side === 'long' ? 1 : -1;
            const fmt = (n: number | null, d = 2) =>
              n === null ? '—' : `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(d)}`;
            const exitFee = price === null ? null : (price * open.quantity * feeBps) / 10000;
            const move = price === null ? null : ((price - open.entry) / open.entry) * 100 * sign;
            const left = 12 + tag.column * 300;
            return (
              <div key={tagKey}>
                <div
                  className={`position-tag ${value === null ? 'flat' : value >= 0 ? 'win' : 'loss'} is-selected`}
                  style={{ top: tag.displayY, left }}
                  onMouseEnter={() => setHover(tagKey)}
                  tabIndex={0}
                  onFocus={() => setHover(tagKey)}
                  onBlur={() => setHover(null)}
                  onClick={() => onSelectPosition?.(open.id)}
                  onMouseLeave={() => setHover(null)}
                >
                  <span>
                    {label}
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
      {trade && !candles.some((c) => c.time === Math.floor(trade.entry_time / 900) * 900) && (
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
