import { useEffect, useRef, useState } from "react";
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
} from "lightweight-charts";
import type { Candle, MtfObservationSnapshot } from "../types/trading";
import type {
  BrokerPosition,
  LearnerAnalysis,
} from "../types/exness-execution";
import { formatPrice, observationStatusLabels } from "../lib/format";
import { placeChartTags } from "../lib/chart-tags";

export type ObservationChartSnapshot = Omit<
  MtfObservationSnapshot,
  "status" | "trend"
> & {
  status: string;
  trend: string | null;
};

export function MtfObservationChart({
  candles,
  snapshot,
  analysis = null,
  livePrice = null,
  liveCandles = [],
  executions = [],
  selectedPositionId,
  frameSeconds = 900,
  onLoadOlder,
  trendLabel = "Xu hướng 4h",
  levelLabel = "Mức breakout",
  dark = false,
  pnlOf,
  pnlAt,
  onClosePosition,
  onSelectPosition,
  title = "BTC_USDT perpetual · 15m (vùng tham chiếu từ 1h) · Chỉ quan sát, không gửi lệnh",
}: {
  candles: Pick<Candle, "time" | "open" | "high" | "low" | "close">[];
  snapshot: ObservationChartSnapshot | null;
  analysis?: LearnerAnalysis | null;
  title?: string;
  livePrice?: { price: number; label: string } | null;
  liveCandles?: Pick<Candle, "time" | "open" | "high" | "low" | "close">[];
  executions?: BrokerPosition[];
  selectedPositionId?: string;
  frameSeconds?: number;
  onLoadOlder?: () => void;
  trendLabel?: string;
  levelLabel?: string;
  dark?: boolean;
  pnlOf?: (position: BrokerPosition) => number | null;
  pnlAt?: (position: BrokerPosition, price: number) => number | null;
  onClosePosition?: (position: BrokerPosition) => void;
  onSelectPosition?: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ranges = useRef<ISeriesApi<"Line">[]>([]);
  const lines = useRef<IPriceLine[]>([]);
  const analysisLines = useRef<IPriceLine[]>([]);
  const fitted = useRef(false);
  const loadOlder = useRef(onLoadOlder);
  loadOlder.current = onLoadOlder;
  const previousTimes = useRef<number[]>([]);
  const [tags, setTags] = useState<
    {
      id: string;
      kind: "entry" | "stop" | "target";
      y: number;
      displayY: number;
      column: number;
    }[]
  >([]);
  const openRows = executions.filter(
    (row) =>
      row.status === "open" && row.open_price != null && row.open_price > 0,
  );
  const activeId = selectedPositionId ?? openRows[0]?.position_id;
  const openKey =
    `${activeId}:` +
    openRows
      .map(
        (row) =>
          `${row.position_id}:${row.open_price}:${row.stop}:${row.target}`,
      )
      .join("|");
  useEffect(() => {
    if (!openKey) {
      setTags([]);
      return;
    }
    let frame = 0;
    let last = "";
    const tick = () => {
      const priceSeries = series.current;
      if (priceSeries) {
        const next = openRows.flatMap((row) =>
          (
            [
              ["entry", row.open_price],
              ["stop", row.stop],
              ["target", row.target],
            ] as const
          ).flatMap(([kind, price]) => {
            if (kind !== "entry" && row.position_id !== activeId) return [];
            if (price == null || !Number.isFinite(price) || price <= 0)
              return [];
            const y = priceSeries.priceToCoordinate(price);
            return y === null
              ? []
              : [{ id: row.position_id, kind, y: Math.round(y) }];
          }),
        );
        const placed = placeChartTags(
          next,
          Math.max(0, (container.current?.clientHeight ?? 0) - 30),
        );
        const key = placed
          .map(
            (tag) =>
              `${tag.id}:${tag.kind}:${tag.y}:${tag.displayY}:${tag.column}`,
          )
          .join("|");
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
          color: dark ? "#0b100e" : "#fcfdf9",
        },
        textColor: dark ? "#899b91" : "#63746a",
        fontFamily: "'DM Sans', sans-serif",
        fontSize: 13,
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: dark ? "#1a2420" : "#eef1e9" },
        horzLines: { color: dark ? "#1a2420" : "#eef1e9" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 8 },
      rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
    });
    series.current = chart.current.addSeries(CandlestickSeries, {
      upColor: "#2c856c",
      downColor: "#bd7164",
      wickUpColor: "#2c856c",
      wickDownColor: "#bd7164",
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

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries || !livePrice) return;
    const line = priceSeries.createPriceLine({
      price: livePrice.price,
      color: "#287fa3",
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
    const added =
      oldFirst === undefined
        ? 0
        : candles.findIndex((bar) => bar.time === oldFirst);
    series.current.setData(
      [
        ...candles,
        ...liveCandles.filter(
          (candle) => candle.time > (candles.at(-1)?.time ?? -1),
        ),
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
      const start = Math.max(
        candles[0]?.time ?? snapshot.range_start,
        snapshot.range_start,
      );
      const end = Math.min(
        candles.at(-1)?.time ?? snapshot.range_end,
        snapshot.range_end,
      );
      if (end > start) {
        for (const [val, color] of [
          [snapshot.range_high, "#b59854"],
          [snapshot.range_low, "#b59854"],
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
            color: "#b59854",
            lineWidth: 1,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title:
              levelLabel === "Mức breakout"
                ? `VÙNG PHÁ (${snapshot.side?.toUpperCase() ?? "CHỜ"})`
                : levelLabel,
          }),
        );
      }
      if (snapshot.stop !== null) {
        lines.current.push(
          priceSeries.createPriceLine({
            price: snapshot.stop,
            color: "#bd7164",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "SL THAM CHIẾU",
          }),
        );
      }
      if (snapshot.target !== null) {
        lines.current.push(
          priceSeries.createPriceLine({
            price: snapshot.target,
            color: "#2c856c",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "TP THAM CHIẾU",
          }),
        );
      }

      const map: [
        number | null,
        string,
        string,
        "arrowUp" | "arrowDown" | "circle",
      ][] = [
        [
          snapshot.breakout_time === null
            ? null
            : snapshot.breakout_time + 2700,
          "BREAKOUT 1H",
          "#b59854",
          "circle",
        ],
        [snapshot.touch_time, "RETEST 15M", "#557fa1", "circle"],
        [
          ["cancelled", "expired"].includes(snapshot.status)
            ? snapshot.candle_time
            : null,
          snapshot.status === "expired" ? "HẾT HẠN" : "HỦY SETUP",
          "#bd7164",
          "circle",
        ],
        [
          snapshot.confirm_time,
          "XÁC NHẬN 15M",
          "#2c856c",
          snapshot.side === "long" ? "arrowUp" : "arrowDown",
        ],
      ];
      for (const [timestamp, label, color, shape] of map) {
        if (timestamp === null) continue;
        const bucket = Math.floor(timestamp / 900) * 900;
        if (candles.some((c) => c.time === bucket)) {
          points.push({
            time: bucket as UTCTimestamp,
            position: snapshot.side === "short" ? "aboveBar" : "belowBar",
            color,
            shape,
            text: label,
          });
        }
      }
    }
    const openTrades = executions.filter((trade) => trade.status === "open");
    const activeId = selectedPositionId ?? openTrades[0]?.position_id;
    for (const trade of openTrades) {
      const isSelected = trade.position_id === activeId;
      for (const deal of trade.deals) {
        if (deal.type !== "open") continue;
        const bucket =
          Math.floor(deal.event_time / frameSeconds) * frameSeconds;
        if (!candles.some((candle) => candle.time === bucket)) continue;
        points.push({
          time: bucket as UTCTimestamp,
          position: trade.side === "buy" ? "belowBar" : "aboveBar",
          color: isSelected ? "#557fa1" : "#33443d",
          shape: trade.side === "buy" ? "arrowUp" : "arrowDown",
          text: `${trade.side?.toUpperCase()} #${trade.position_id} @ ${deal.price}`,
        });
      }
      if (isSelected) {
        for (const [price, color, title] of [
          [trade.open_price, "#557fa1", `ENTRY #${trade.position_id}`],
          [trade.stop, "#bd7164", `SL #${trade.position_id}`],
          [trade.target, "#2c856c", `TP #${trade.position_id}`],
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
      } else if (trade.open_price && trade.open_price > 0) {
        lines.current.push(
          priceSeries.createPriceLine({
            price: trade.open_price,
            color: "#2a3b33",
            title: `#${trade.position_id}`,
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: false,
          }),
        );
      }
    }
    markers.current?.setMarkers(
      points.sort((a, b) => Number(a.time) - Number(b.time)),
    );
  }, [
    snapshot,
    candles,
    levelLabel,
    executions,
    selectedPositionId,
    frameSeconds,
  ]);

  useEffect(() => {
    const priceSeries = series.current;
    if (!priceSeries) return;
    analysisLines.current.forEach((line) => priceSeries.removePriceLine(line));
    analysisLines.current = [];
    if (!analysis) return;
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
    add(
      levels.m15_donchian?.high,
      "#b59854",
      "BOT · Donchian20 cao",
      LineStyle.Dotted,
    );
    add(
      levels.m15_donchian?.low,
      "#b59854",
      "BOT · Donchian20 thấp",
      LineStyle.Dotted,
    );
    add(levels.m15_ema20, "#557fa1", "BOT · EMA20 M15", LineStyle.Dotted);
    if (last != null) {
      const above = levels.barriers
        .filter((row) => row.side === "long" && row.price > last)
        .sort((a, b) => a.price - b.price)[0];
      const below = levels.barriers
        .filter((row) => row.side === "short" && row.price < last)
        .sort((a, b) => b.price - a.price)[0];
      if (above)
        add(
          above.price,
          "#2c856c",
          `CẢN TRÊN · ${above.reason}`,
          LineStyle.LargeDashed,
        );
      if (below)
        add(
          below.price,
          "#bd7164",
          `CẢN DƯỚI · ${below.reason}`,
          LineStyle.LargeDashed,
        );
    }
    if (signal) {
      const tag = `${signal.strategy} ${signal.side.toUpperCase()}`;
      add(
        signal.projected_entry,
        "#8a7bb5",
        `DỰ KIẾN VÀO · ${tag}`,
        LineStyle.Dashed,
      );
      add(
        signal.stop,
        "#bd7164",
        "DỰ KIẾN SL · chưa vào lệnh",
        LineStyle.Dashed,
      );
      add(
        signal.projected_tp,
        "#2c856c",
        "DỰ KIẾN TP · chưa vào lệnh",
        LineStyle.Dashed,
      );
    }
    return () => {
      analysisLines.current.forEach((line) =>
        priceSeries.removePriceLine(line),
      );
      analysisLines.current = [];
    };
  }, [analysis, candles]);

  return (
    <div className="trade-chart-wrap">
      <div className="chart-ohlc">{title}</div>
      <div className="chart-price-surface">
        <div
          ref={container}
          className="trade-candlestick-chart"
          role="img"
          aria-label="Chart quan sát đa khung"
        />
        <div className="position-tag-layer">
          {!snapshot &&
            (() => {
              return tags.map((tag) => {
                const row = openRows.find(
                  (item) => item.position_id === tag.id,
                );
                if (!row) return null;
                const isSelected = tag.id === activeId;
                if (tag.kind !== "entry" && !isSelected) return null;
                const key = `${tag.id}-${tag.kind}`;
                if (tag.kind !== "entry") {
                  const price = tag.kind === "stop" ? row.stop : row.target;
                  const value =
                    price == null ? null : (pnlAt?.(row, price) ?? null);
                  if (value == null) return null;
                  return (
                    <div
                      key={key}
                      className={`position-tag ${value >= 0 ? "win" : "loss"} ${isSelected ? "is-selected" : "is-faded"}`}
                      style={{ top: tag.displayY, left: 12 + tag.column * 300 }}
                      title={`#${row.position_id} · ${price} · P/L dự kiến, chưa trừ phí/swap`}
                    >
                      <span>
                        {tag.kind === "stop" ? "SL" : "TP"} #{row.position_id}
                      </span>
                      <strong>
                        {value >= 0 ? "+" : "−"}
                        {Math.abs(value).toFixed(2)} USD
                      </strong>
                    </div>
                  );
                }
                const pnl = pnlOf?.(row) ?? null;
                const tone = pnl == null ? "flat" : pnl >= 0 ? "win" : "loss";
                return (
                  <div
                    key={key}
                    className={`position-tag ${tone} ${isSelected ? "is-selected" : "is-faded"}`}
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
                        {row.volume} <small>#{row.position_id}</small>
                      </span>
                      <strong>
                        {pnl == null
                          ? "—"
                          : `${pnl >= 0 ? "+" : "−"}${Math.abs(pnl).toFixed(2)} USD`}
                      </strong>
                    </button>
                    {isSelected &&
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
        <div
          className="broker-chart-legend"
          aria-label="Chú thích dấu khớp lệnh sàn"
        >
          <span>
            <span
              className="broker-marker-dot marker-entry"
              aria-hidden="true"
            />{" "}
            Mũi tên: Entry vị thế đang mở
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
      <div className="chart-legend">
        {snapshot && (
          <strong>
            {trendLabel}:{" "}
            {snapshot.trend === "up"
              ? "Tăng"
              : snapshot.trend === "down"
                ? "Giảm"
                : snapshot.trend === "sideway"
                  ? "Đi ngang"
                  : "Chưa rõ"}{" "}
            · {observationStatusLabels[snapshot.status] ?? snapshot.status} ·
            Vùng {snapshot.level ? formatPrice(snapshot.level) : "—"}
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
    </div>
  );
}
