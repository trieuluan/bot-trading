import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  LineSeries,
  ColorType,
  LineStyle,
  LineType,
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
import type { BrokerPosition, LearnerAnalysis } from '../types/exness-execution';
import type { ForexEntryIntent, ForexRecordedSignal } from '../types/forex-entry';
import { intentEvents } from '../lib/forex-entry-lifecycle';
import { formatForexPrice as formatPrice, observationStatusLabels } from '../lib/format';
import { CONTEXT_STRATEGY_KEY } from './StrategyOverlayMenu';
import { chartTreeTag, treeLabel } from '../lib/forex-tree-label';
import { placeChartTags } from '../lib/chart-tags';
import { CHART_RIGHT_SPACE, projectOverlayPoints, recentChartRange } from '../lib/chart-timeframes';
import { chartTimeOptions } from '../lib/chart-time';
import { useTimeZone } from '../lib/time-zone';
import { positionGroupId, sumFloating, type PositionGroup } from '../lib/forex-position-groups';

export type ObservationChartSnapshot = Omit<MtfObservationSnapshot, 'status' | 'trend'> & {
  status: string;
  trend: string | null;
};

export function MtfObservationChart({
  candles,
  snapshot,
  analysis = null,
  entryIntents,
  recordedSignals = [],
  selectedSetupTime,
  shown = [],
  livePrice = null,
  liveCandles = [],
  executions = [],
  positionGroups = [],
  selectedPositionId,
  frameSeconds = 900,
  pricePrecision = 3,
  onLoadOlder,
  trendLabel = 'Xu hướng 4h',
  levelLabel = 'Mức breakout',
  dark = false,
  pnlOf,
  pnlAt,
  onClosePosition,
  onSelectPosition,
  title = 'BTC_USDT perpetual · 15m (vùng tham chiếu từ 1h) · Chỉ quan sát, không gửi lệnh',
}: {
  candles: Pick<Candle, 'time' | 'open' | 'high' | 'low' | 'close'>[];
  snapshot: ObservationChartSnapshot | null;
  analysis?: LearnerAnalysis | null;
  entryIntents?: ForexEntryIntent[];
  recordedSignals?: ForexRecordedSignal[];
  selectedSetupTime?: number;
  shown?: string[];
  title?: string;
  livePrice?: { price: number; label: string; color?: string }[] | null;
  liveCandles?: Pick<Candle, 'time' | 'open' | 'high' | 'low' | 'close'>[];
  executions?: BrokerPosition[];
  positionGroups?: PositionGroup[];
  selectedPositionId?: string;
  frameSeconds?: number;
  pricePrecision?: number;
  onLoadOlder?: () => void;
  trendLabel?: string;
  levelLabel?: string;
  dark?: boolean;
  pnlOf?: (position: BrokerPosition) => number | null;
  pnlAt?: (position: BrokerPosition, price: number) => number | null;
  onClosePosition?: (position: BrokerPosition) => void;
  onSelectPosition?: (id: string) => void;
}) {
  const [timeZone] = useTimeZone();
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ranges = useRef<ISeriesApi<'Line'>[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const analysisLines = useRef<IPriceLine[]>([]);
  const zoneSeries = useRef<ISeriesApi<'Line'>[]>([]);
  const fitted = useRef(false);
  const loadOlder = useRef(onLoadOlder);
  loadOlder.current = onLoadOlder;
  const previousTimes = useRef<number[]>([]);
  const [setupBoxes, setSetupBoxes] = useState<
    {
      id: string;
      x: number;
      y: number;
      width: number;
      height: number;
      color: string;
      label: string;
    }[]
  >([]);
  const [tags, setTags] = useState<
    {
      id: string;
      kind: 'entry' | 'stop' | 'target';
      y: number;
      displayY: number;
      column: number;
    }[]
  >([]);
  const openRows = executions.filter(
    (row) => row.status === 'open' && row.open_price != null && row.open_price > 0,
  );
  const activeId = selectedPositionId ?? openRows[0]?.position_id;
  const activeGroup = positionGroups.find((group) => group.legs.some((leg) => leg.position_id === activeId));
  const selectedGroupId = activeGroup?.id;
  const openKey =
    `${activeId}:${selectedGroupId}:` +
    openRows
      .map((row) => `${row.position_id}:${row.open_price}:${row.stop}:${row.target}`)
      .join('|');
  useEffect(() => {
    if (!openKey) {
      setTags([]);
      return;
    }
    let frame = 0;
    let last = '';
    const tick = () => {
      const priceSeries = series.current;
      if (priceSeries) {
        const next = openRows.flatMap((row) =>
          (
            [
              ['entry', row.open_price],
              ['stop', row.stop],
              ['target', row.target],
            ] as const
          ).flatMap(([kind, price]) => {
            const group = positionGroups.find((group) => group.id === positionGroupId(row));
            const representative = group?.legs.find((leg) => leg.status === 'open');
            if (kind === 'entry' && representative && representative.position_id !== row.position_id) return [];
            if (kind !== 'entry' && (group ? group.id !== selectedGroupId : row.position_id !== activeId)) return [];
            if (price == null || !Number.isFinite(price) || price <= 0) return [];
            const y = priceSeries.priceToCoordinate(price);
            return y === null ? [] : [{ id: row.position_id, kind, y: Math.round(y) }];
          }),
        );
        const placed = placeChartTags(
          next,
          Math.max(0, (container.current?.clientHeight ?? 0) - 30),
        );
        const key = placed
          .map((tag) => `${tag.id}:${tag.kind}:${tag.y}:${tag.displayY}:${tag.column}`)
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
  }, [openKey]);

  useEffect(() => {
    if (!container.current) return;
    chart.current = createChart(container.current, {
      autoSize: true,
      layout: {
        background: {
          type: ColorType.Solid,
          color: dark ? '#0b100e' : '#fcfdf9',
        },
        textColor: dark ? '#899b91' : '#63746a',
        fontFamily: "'DM Sans', sans-serif",
        fontSize: 13,
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: dark ? '#1a2420' : '#eef1e9' },
        horzLines: { color: dark ? '#1a2420' : '#eef1e9' },
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
      if (range && range.from < 30) loadOlder.current?.();
    });
    return () => {
      chart.current?.remove();
      chart.current = null;
      series.current = null;
      markers.current = null;
      lines.current = [];
      analysisLines.current = [];
      ranges.current = [];
      fitted.current = false;
      previousTimes.current = [];
    };
  }, []);

  // Nhãn trục và crosshair theo múi giờ hiển thị; dữ liệu nến vẫn là timestamp UTC.
  useEffect(() => {
    chart.current?.applyOptions(chartTimeOptions(timeZone));
  }, [timeZone]);

  useEffect(() => {
    series.current?.applyOptions({
      priceFormat: { type: 'price', precision: pricePrecision, minMove: 10 ** -pricePrecision },
    });
  }, [pricePrecision]);

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries) return;
    const virtualLines: IPriceLine[] = [];
    for (const intent of entryIntents ?? []) {
      const waiting = intent.status === 'waiting';
      const triggered =
        ['triggered', 'submitting', 'uncertain'].includes(intent.status) ||
        (intent.status === 'submitted' &&
          intent.broker_fills?.some((fill) =>
            executions.some(
              (position) => position.position_id === fill.position_id && position.status === 'open',
            ),
          ));
      for (const branch of ['limit', 'stop'] as const) {
        const price = branch === 'limit' ? intent.limit_price : intent.stop_price;
        if (!price) continue;
        if (waiting || (triggered && intent.winner === branch)) {
        virtualLines.push(
          priceSeries.createPriceLine({
            price,
            color: branch === 'limit' ? '#138b79' : '#cb8428',
            lineWidth: waiting ? 1 : 2,
            lineStyle: waiting ? LineStyle.Dashed : LineStyle.Solid,
            axisLabelVisible: true,
            title: `${branch === 'limit' ? 'Limit giữa' : 'Stop phá nến'} ảo · ${waiting ? 'chờ giá' : 'đã kích hoạt'}`,
          }),
        );
        } else {
          virtualLines.push(
            priceSeries.createPriceLine({
              price,
              color: '#8a948d',
              lineWidth: 1,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: false,
              title: `${branch === 'limit' ? 'Limit' : 'Stop'} ảo · setup đã chọn`,
            }),
          );
        }
      }
      for (const [price, title, color] of [
        [intent.signal?.stop, 'SL setup · không phải SL sàn', '#bd7164'],
        [intent.signal?.target, 'TP setup · không phải TP sàn', '#2c856c'],
      ] as const) {
        if (price == null || !Number.isFinite(price) || price <= 0) continue;
        virtualLines.push(priceSeries.createPriceLine({
          price, title, color, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false,
        }));
      }
    }
    return () => {
      if (series.current === priceSeries)
        virtualLines.forEach((line) => priceSeries.removePriceLine(line));
    };
  }, [entryIntents, executions]);

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries) return;
    const hasLive = Boolean(livePrice?.length);
    priceSeries.applyOptions({ lastValueVisible: !hasLive, priceLineVisible: !hasLive });
    if (!livePrice?.length) return;
    const created = livePrice.map((row) =>
      priceSeries.createPriceLine({
        price: row.price,
        color: row.color ?? '#287fa3',
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: row.label,
      }),
    );
    return () => {
      if (series.current === priceSeries)
        created.forEach((line) => priceSeries.removePriceLine(line));
    };
  }, [livePrice]);

  useEffect(() => {
    if (!series.current) return;
    const visible = chart.current?.timeScale().getVisibleLogicalRange();
    const oldFirst = previousTimes.current[0];
    const added = oldFirst === undefined ? 0 : candles.findIndex((bar) => bar.time === oldFirst);
    const displayed = [
      ...candles,
      ...liveCandles.filter((candle) => candle.time > (candles.at(-1)?.time ?? -1)),
    ];
    series.current.setData(
      displayed.map((candle) => ({ ...candle, time: candle.time as UTCTimestamp })),
    );
    previousTimes.current = candles.map((bar) => bar.time);
    if (visible && added > 0) {
      chart.current?.timeScale().setVisibleLogicalRange({
        from: visible.from + added,
        to: visible.to + added,
      });
    }
    if (!fitted.current && candles.length) {
      chart.current?.timeScale().setVisibleLogicalRange(recentChartRange(displayed.length));
      fitted.current = true;
    }
  }, [candles, liveCandles]);

  const focusedSetup = useRef<string | null>(null);
  useEffect(() => {
    if (selectedSetupTime == null) {
      if (focusedSetup.current && candles.length)
        chart.current?.timeScale().setVisibleLogicalRange(recentChartRange(candles.length));
      focusedSetup.current = null;
      return;
    }
    const key = `${selectedSetupTime}:${frameSeconds}`;
    if (focusedSetup.current === key || !chart.current) return;
    const index = candles.findIndex((candle) => candle.time === Math.floor(selectedSetupTime / frameSeconds) * frameSeconds);
    if (index < 0) return;
    chart.current.timeScale().setVisibleLogicalRange({ from: index - 12, to: index + 24 });
    focusedSetup.current = key;
  }, [selectedSetupTime, frameSeconds, candles]);

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
    const candleTimes = new Set(candles.map((candle) => candle.time));
    if (liveCandles.at(-1)) candleTimes.add(liveCandles.at(-1)!.time);
    const events = new Map<string, SeriesMarker<Time> & { count: number }>();
    const addEvent = (time: number, side: string, text: string, color: string) => {
      const bucket = Math.floor(time / frameSeconds) * frameSeconds;
      if (time > Date.now() / 1000 || !candleTimes.has(bucket)) return;
      const key = `${bucket}:${side}:${text}`;
      const previous = events.get(key);
      if (previous) previous.count += 1;
      else events.set(key, { time: bucket as UTCTimestamp, position: side === 'short' ? 'aboveBar' : 'belowBar',
        shape: 'circle', text, color, count: 1 });
    };
    for (const signal of recordedSignals) {
      const version = signal.strategy.startsWith('hast4_') ? 'V4' : 'V3';
      addEvent(signal.available_at, signal.side,
        `TÍN HIỆU ${version} ${signal.side === 'long' ? 'MUA' : 'BÁN'}`, '#7e9eb7');
    }
    for (const intent of entryIntents ?? []) {
      for (const event of intentEvents(intent)) {
        addEvent(event.time, intent.side, event.text, event.color);
      }
    }
    for (const event of events.values()) {
      const { count, ...marker } = event;
      points.push({ ...marker, text: `${marker.text}${count > 1 ? ` ×${count}` : ''}` });
    }
    const chosen = executions.find((trade) => trade.position_id === selectedPositionId);
    const chosenGroup = positionGroups.find((group) => group.legs.some((leg) => leg.position_id === chosen?.position_id));
    const openTrades = executions.filter((trade) => trade.status === 'open' || chosenGroup?.legs.some((leg) => leg.position_id === trade.position_id));
    const activeId = selectedPositionId ?? openTrades[0]?.position_id;
    const marked = new Set<string>();
    for (const trade of openTrades) {
      const group = positionGroups.find((group) => group.id === positionGroupId(trade));
      const isSelected = group ? group.legs.some((leg) => leg.position_id === activeId) : trade.position_id === activeId;
      for (const deal of trade.deals) {
        if (deal.type !== 'open') continue;
        const bucket = Math.floor(deal.event_time / frameSeconds) * frameSeconds;
        if (!candles.some((candle) => candle.time === bucket)) continue;
        const markerId = group?.id ?? trade.position_id;
        if (marked.has(markerId)) continue;
        marked.add(markerId);
        points.push({
          time: bucket as UTCTimestamp,
          position: trade.side === 'buy' ? 'belowBar' : 'aboveBar',
          color: isSelected ? '#557fa1' : '#33443d',
          shape: trade.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: `${trade.side?.toUpperCase()} ${group?.ownership_verified ? 'Setup' : 'Ticket'} #${group?.legs[0].position_id ?? trade.position_id}`,
        });
      }
      if (isSelected) {
        for (const [price, color, title] of [
          [trade.open_price, '#557fa1', `ENTRY #${trade.position_id}`],
          [trade.stop, '#bd7164', `SL #${trade.position_id}`],
          [trade.target, '#2c856c', `TP #${trade.position_id}`],
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
  }, [snapshot, candles, levelLabel, executions, selectedPositionId, frameSeconds, entryIntents,
    recordedSignals, liveCandles.at(-1)?.time]);

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries) return;
    analysisLines.current.forEach((line) => priceSeries.removePriceLine(line));
    analysisLines.current = [];
    if (!analysis) return;
    const on = (name: string) => shown.includes(name);
    const add = (
      price: number | null | undefined,
      color: string,
      title: string,
      style: LineStyle,
    ) => {
      if (price == null || !Number.isFinite(price) || price <= 0) return;
      analysisLines.current.push(
        priceSeries.createPriceLine({
          price,
          color,
          title,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: true,
        }),
      );
    };
    const last = candles.at(-1)?.close;
    const { levels, selected_signal: signal } = analysis;
    if (on(CONTEXT_STRATEGY_KEY) && last != null) {
      for (const frame of ['H1', 'H4']) {
        const above = levels.barriers
          .filter((row) => row.reason.startsWith(frame) && row.side === 'long' && row.price > last)
          .sort((a, b) => a.price - b.price)[0];
        const below = levels.barriers
          .filter((row) => row.reason.startsWith(frame) && row.side === 'short' && row.price < last)
          .sort((a, b) => b.price - a.price)[0];
        if (above) add(above.price, '#2c856c', `CẢN TRÊN · ${above.reason}`, LineStyle.LargeDashed);
        if (below) add(below.price, '#bd7164', `CẢN DƯỚI · ${below.reason}`, LineStyle.LargeDashed);
      }
    }
    if (signal && on(signal.strategy)) {
      const tag = `${signal.strategy} ${signal.side.toUpperCase()}`;
      add(signal.projected_entry, '#557fa1', `THAM CHIẾU VÀO · ${tag}`, LineStyle.Dashed);
      add(signal.projected_stop, '#bd7164', 'SL DỰ KIẾN · không phải lệnh sàn', LineStyle.Dashed);
      add(signal.projected_tp, '#2c856c', 'TP DỰ KIẾN · không phải lệnh sàn', LineStyle.Dashed);
    }
    return () => {
      analysisLines.current.forEach((line) => priceSeries.removePriceLine(line));
      analysisLines.current = [];
    };
  }, [analysis, candles, shown]);

  useEffect(() => {
    const chartApi = chart.current;
    if (!chartApi) return;
    const made: ISeriesApi<'Line'>[] = [];
    if (shown.includes(CONTEXT_STRATEGY_KEY) && candles.length > 20) {
      const frame = { 900: 'M15', 3600: 'H1', 14400: 'H4', 86400: 'D1' }[frameSeconds] ?? '';
      const k = 2 / 21;
      let ema = candles.slice(0, 20).reduce((sum, c) => sum + c.close, 0) / 20;
      const emaPoints = [{ time: candles[19].time as UTCTimestamp, value: ema }];
      const high: { time: UTCTimestamp; value: number }[] = [];
      const low: { time: UTCTimestamp; value: number }[] = [];
      for (let i = 20; i < candles.length; i++) {
        ema = candles[i].close * k + ema * (1 - k);
        emaPoints.push({ time: candles[i].time as UTCTimestamp, value: ema });
        const window = candles.slice(i - 20, i);
        high.push({
          time: candles[i].time as UTCTimestamp,
          value: Math.max(...window.map((c) => c.high)),
        });
        low.push({
          time: candles[i].time as UTCTimestamp,
          value: Math.min(...window.map((c) => c.low)),
        });
      }
      for (const [data, title, color, style, width] of [
        [emaPoints, `EMA20 ${frame}`, '#557fa1', LineStyle.Solid, 2],
        [high, `Donchian20 cao ${frame}`, '#b59854', LineStyle.Dotted, 1],
        [low, `Donchian20 thấp ${frame}`, '#b59854', LineStyle.Dotted, 1],
      ] as const) {
        const line = chartApi.addSeries(LineSeries, {
          title,
          color,
          lineStyle: style,
          lineWidth: width,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null,
        });
        line.setData([...data]);
        made.push(line);
      }
    }
    return () => {
      if (chart.current === chartApi) made.forEach((line) => chartApi.removeSeries(line));
    };
  }, [candles, shown, frameSeconds]);

  useEffect(() => {
    const chartApi = chart.current;
    if (!chartApi || !analysis || !candles.length) return;
    const made: ISeriesApi<'Line'>[] = [];
    const seen = new Set<string>();
    const first = candles[0].time;
    for (const name of shown) {
      const overlay = analysis.strategies[name]?.chart?.overlays;
      for (const line of overlay?.lines ?? []) {
        const key = JSON.stringify([line.label, line.points]);
        if (seen.has(key)) continue;
        seen.add(key);
        const points = line.points.filter((point) => point.time <= analysis.available_at);
        const previous = [...points].reverse().find((point) => point.time < first);
        const visible = points.filter((point) => point.time >= first);
        if (previous) visible.unshift({ ...previous, time: first });
        const last = visible.at(-1);
        if (last && last.time < analysis.available_at)
          visible.push({ ...last, time: analysis.available_at });
        const aligned = new Map<number, number>();
        for (const point of visible)
          aligned.set(Math.ceil(point.time / frameSeconds) * frameSeconds, point.value);
        if (aligned.size < 2) continue;
        const plot = chartApi.addSeries(LineSeries, {
          title: line.label,
          color: line.color,
          lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
          lineType: LineType.WithSteps,
          lineWidth: line.dashed ? 1 : 2,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null,
        });
        plot.setData([...aligned].map(([time, value]) => ({ time: time as UTCTimestamp, value })));
        made.push(plot);
      }
    }
    return () => {
      if (chart.current === chartApi) made.forEach((line) => chartApi.removeSeries(line));
    };
  }, [analysis, candles, shown, frameSeconds]);

  // Vùng setup H1 của cây V2: vẽ theo thời gian (từ nến hình thành tới hạn 4h), không kéo hết chart.
  useEffect(() => {
    const chartApi = chart.current;
    if (!chartApi) return;
    const clear = () => {
      if (chart.current === chartApi)
        zoneSeries.current.forEach((item) => chartApi.removeSeries(item));
      zoneSeries.current = [];
    };
    clear();
    if (!analysis) return clear;
    const first = candles[0]?.time ?? 0;
    const lastTime = Math.max(candles.at(-1)?.time ?? 0, analysis.candle_time + 900);
    for (const [name, row] of Object.entries(analysis.strategies)) {
      if (!shown.includes(name)) continue;
      const hue = [...name].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0) % 360;
      const color = `hsl(${hue}, 65%, 65%)`;
      const id = chartTreeTag(row, name);
      // Mẫu đã chụp (forward) và setup đang hiện tại: cùng setup thì chỉ vẽ mẫu đã chụp.
      for (const [zone, captured] of [
        [row.captured_zone, true],
        [row.zone, false],
      ] as const) {
        if (!zone) continue;
        const from = Math.max(first, zone.from);
        const to = Math.min(zone.to, lastTime + frameSeconds * 8);
        if (to <= from) continue;
        const side = zone.side === 'long' ? 'LONG' : 'SHORT';
        const tag = `${id} ${side}${captured ? ' · đã chụp' : ''}`;
        const rows: [number | null | undefined, string, LineStyle, string, number][] = [
          [zone.level, `${tag} · Setup H1`, LineStyle.Solid, color, 2],
          [zone.invalidation, `${tag} · vô hiệu H1`, LineStyle.Dotted, '#e0877a', 2],
          [zone.stop, `${tag} · SL H1`, LineStyle.LargeDashed, '#bd7164', 1],
          [zone.target, `${tag} · TP cấu trúc`, LineStyle.LargeDashed, '#2c856c', 1],
          [zone.entry, `${tag} · Entry tham chiếu`, LineStyle.SparseDotted, '#557fa1', 1],
        ];
        for (const [price, title, style, tint, width] of rows) {
          if (price == null || !Number.isFinite(price) || price <= 0) continue;
          const line = chartApi.addSeries(LineSeries, {
            title,
            color: tint,
            lineWidth: width as 1 | 2,
            lineStyle: style,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
            autoscaleInfoProvider: () => null,
          });
          line.setData(
            projectOverlayPoints(
              [
                { time: from, value: price },
                { time: to, value: price },
              ],
              frameSeconds,
            ).map((point) => ({ ...point, time: point.time as UTCTimestamp })),
          );
          zoneSeries.current.push(line);
        }
      }
    }
    return clear;
  }, [analysis, candles, shown, frameSeconds]);

  useEffect(() => {
    let frame = 0;
    let previous = '';
    const tick = () => {
      const chartApi = chart.current;
      const priceSeries = series.current;
      if (chartApi && priceSeries && container.current) {
        const boxes = Object.entries(analysis?.strategies ?? {}).flatMap(([name, row]) => {
          if (!shown.includes(name)) return [];
          return [row.captured_zone, row.zone].flatMap((zone, index) => {
            if (!zone || !Number.isFinite(zone.level) || !Number.isFinite(zone.invalidation))
              return [];
            const first = candles[0]?.time ?? 0;
            const last = Math.max(candles.at(-1)?.time ?? 0, (analysis?.candle_time ?? 0) + 900);
            const from = Math.max(first, zone.from);
            const to = Math.min(zone.to, last + frameSeconds * 8);
            if (to <= from) return [];
            const x0 = chartApi
              .timeScale()
              .timeToCoordinate((Math.floor(from / frameSeconds) * frameSeconds) as UTCTimestamp);
            const x1 = chartApi
              .timeScale()
              .timeToCoordinate((Math.floor(to / frameSeconds) * frameSeconds) as UTCTimestamp);
            const y0 = priceSeries.priceToCoordinate(zone.level);
            const y1 = priceSeries.priceToCoordinate(zone.invalidation);
            if (x0 == null || x1 == null || y0 == null || y1 == null) return [];
            const x = Math.max(0, Math.min(x0, x1));
            const rawY = Math.min(y0, y1);
            const width = Math.max(0, Math.min(chartApi.timeScale().width(), Math.max(x0, x1)) - x);
            const y = Math.max(0, rawY);
            const height = Math.max(
              0,
              Math.min(priceSeries.getPane().getHeight(), Math.max(y0, y1)) - y,
            );
            if (!width || !height) return [];
            const hue =
              [...name].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0) % 360;
            return [
              {
                id: `${name}:${index}`,
                x,
                y,
                width,
                height,
                color: `hsl(${hue}, 65%, 65%)`,
                label: `${chartTreeTag(row, name)} · ${zone.side === 'long' ? 'LONG' : 'SHORT'} · ${index === 0 ? 'đã chụp' : 'setup H1'}`,
              },
            ];
          });
        });
        const key = JSON.stringify(boxes);
        if (key !== previous) {
          previous = key;
          setSetupBoxes(boxes);
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [analysis, candles, shown, frameSeconds]);

  return (
    <div className="trade-chart-wrap forex-observation-chart">
      <div className="chart-ohlc">{title}</div>
      {analysis && shown.some((name) => analysis.strategies[name]) && (
        <div className="forex-overlay-readout">
          {shown
            .filter((name) => analysis.strategies[name])
            .map((name) => {
              const row = analysis.strategies[name];
              const overlay = row.chart?.overlays;
              return (
                <details key={name}>
                  <summary>{chartTreeTag(row, name)} · Chỉ báo & mức giá</summary>
                  <div>{treeLabel(row, name).full}</div>
                  <div>
                    {overlay
                      ? [
                          ...new Set(overlay.lines.map((line) => line.label)),
                          ...overlay.notes,
                        ].join(' · ')
                      : 'Chờ dữ liệu vẽ chỉ báo của cây'}
                  </div>
                  {[row.captured_zone, row.zone].map(
                    (zone, index) =>
                      zone && (
                        <div key={index}>
                          {index === 0 ? 'Đã chụp' : 'Setup hiện tại'} · {zone.family} ·{' '}
                          {zone.side === 'long' ? 'LONG' : 'SHORT'}
                          {zone.target_reason && ` · TP: ${zone.target_reason}`}
                        </div>
                      ),
                  )}
                </details>
              );
            })}
        </div>
      )}
      <div className="chart-price-surface">
        <div
          ref={container}
          className="trade-candlestick-chart"
          role="img"
          aria-label="Chart quan sát đa khung"
        />
        <svg
          className="forex-setup-zones"
          aria-label="Vùng setup H1 từ mức cấu trúc tới mức vô hiệu"
        >
          {setupBoxes.map((box) => (
            <g key={box.id}>
              <rect
                x={box.x}
                y={box.y}
                width={box.width}
                height={box.height}
                fill={box.color}
                fillOpacity={0.14}
              />
              {box.width >= 50 && (
                <text
                  x={box.x + 4}
                  y={Math.max(12, box.y + 12)}
                  fill={box.color}
                  fontSize={10}
                  className="forex-zone-text"
                >
                  {box.width < 120 ? box.label.slice(0, 14) + '…' : box.label}
                </text>
              )}
            </g>
          ))}
        </svg>
        <div className="position-tag-layer">
          {!snapshot &&
            (() => {
              return tags.map((tag) => {
                const row = openRows.find((item) => item.position_id === tag.id);
                if (!row) return null;
                const group = positionGroups.find((group) => group.id === positionGroupId(row));
                const isSelected = group ? group.id === selectedGroupId : tag.id === activeId;
                if (tag.kind !== 'entry' && !isSelected) return null;
                const key = `${tag.id}-${tag.kind}`;
                if (tag.kind !== 'entry') {
                  const price = tag.kind === 'stop' ? row.stop : row.target;
                  const value = price == null ? null : (pnlAt?.(row, price) ?? null);
                  if (value == null) return null;
                  return (
                    <div
                      key={key}
                      className={`position-tag ${value >= 0 ? 'win' : 'loss'} ${isSelected ? 'is-selected' : 'is-faded'}`}
                      style={{ top: tag.displayY, left: 12 + tag.column * 300 }}
                      title={`#${row.position_id} · ${price} · P/L dự kiến, chưa trừ phí/swap`}
                    >
                      <span>
                        {tag.kind === 'stop' ? 'SL' : 'TP'} #{row.position_id}
                      </span>
                      <strong>
                        {value >= 0 ? '+' : '−'}
                        {Math.abs(value).toFixed(2)} USD
                      </strong>
                    </div>
                  );
                }
                const pnl = group && pnlOf ? sumFloating(group, pnlOf) : pnlOf?.(row) ?? null;
                const tone = pnl == null ? 'flat' : pnl >= 0 ? 'win' : 'loss';
                return (
                  <div
                    key={key}
                    className={`position-tag ${tone} ${isSelected ? 'is-selected' : 'is-faded'}`}
                    style={{ top: tag.displayY, left: 12 + tag.column * 300 }}
                    title={`#${row.position_id} · Giá vào ${row.open_price} · P/L ước tính, chưa trừ phí/swap`}
                  >
                    <button
                      type="button"
                      className="position-tag-select"
                      aria-label={`Chọn vị thế #${row.position_id}`}
                      aria-pressed={isSelected}
                      onClick={() => onSelectPosition?.(row.position_id)}
                    >
                      <span className="tag-vol">
                        {group?.remaining_volume ?? row.remaining_volume ?? row.volume} <small>{group?.ownership_verified ? 'Setup' : 'Ticket'} #{group?.legs[0].position_id ?? row.position_id}</small>
                      </span>
                      <strong>
                        {pnl == null
                          ? '—'
                          : `${pnl >= 0 ? '+' : '−'}${Math.abs(pnl).toFixed(2)} USD`}
                      </strong>
                    </button>
                    {isSelected && !group &&
                      onClosePosition &&
                      row.signal_time != null &&
                      !row.close_requested && (
                        <button
                          type="button"
                          aria-label={`Đóng vị thế #${row.position_id}`}
                          onClick={() => {
                            onClosePosition(row);
                          }}
                        >
                          ×
                        </button>
                      )}
                  </div>
                );
              });
            })()}
          <svg className="position-tag-leaders" aria-hidden="true">
            {!snapshot &&
              tags.map((tag) =>
                tag.y !== tag.displayY ? (
                  <line
                    key={`${tag.id}-${tag.kind}`}
                    x1={6}
                    y1={tag.y}
                    x2={12 + tag.column * 300}
                    y2={tag.displayY}
                  />
                ) : null,
              )}
          </svg>
        </div>
      </div>
      {executions && executions.length > 0 && !snapshot && (
        <div className="broker-chart-legend" aria-label="Chú thích dấu khớp lệnh sàn">
          <span>
            <span className="broker-marker-dot marker-entry" aria-hidden="true" /> Mũi tên: Entry vị
            thế đang mở
          </span>
          {selectedPositionId && (
            <>
              <span className="stop-legend">--- SL sàn</span>
              <span className="target-legend">--- TP sàn</span>
              <span className="entry-legend">--- Giá vào</span>
            </>
          )}
        </div>
      )}
      {snapshot && (
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
          {snapshot && snapshot.stop !== null && (
            <span className="stop-legend">— SL tham chiếu</span>
          )}
          {snapshot && snapshot.target !== null && (
            <span className="target-legend">— TP tham chiếu</span>
          )}
        </div>
      )}
    </div>
  );
}
