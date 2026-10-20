import type {
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';

export type ChartLabel = {
  time: number;
  price: number;
  above: boolean;
  color: string;
  text: string;
};

const MAX_CHARS = 44;
const HEIGHT = 18;

export function labelText(text: string) {
  return text.length > MAX_CHARS ? `${text.slice(0, MAX_CHARS - 1)}…` : text;
}

export function stackLabels<T extends { time: number; above: boolean }>(labels: T[]) {
  const seen = new Map<string, number>();
  return labels.map((label) => {
    const key = `${label.time}:${label.above}`;
    const slot = seen.get(key) ?? 0;
    seen.set(key, slot + 1);
    return { ...label, slot };
  });
}

export function drawOrder<T extends { time: number }>(labels: T[], hovered: number | null) {
  return [...labels].sort((a, b) => Number(a.time === hovered) - Number(b.time === hovered));
}

export class ChartLabels implements ISeriesPrimitive<Time> {
  private labels: ChartLabel[] = [];
  private hovered: number | null = null;
  private param: SeriesAttachedParameter<Time> | null = null;
  private series: ISeriesApi<'Candlestick'> | null = null;

  private hoverOnly: boolean;

  constructor(hoverOnly = false) {
    this.hoverOnly = hoverOnly;
  }

  attached(param: SeriesAttachedParameter<Time>) {
    this.param = param;
    this.series = param.series as ISeriesApi<'Candlestick'>;
  }

  detached() {
    this.param = null;
    this.series = null;
  }

  set(labels: ChartLabel[]) {
    this.labels = labels;
    this.param?.requestUpdate();
  }

  hover(time: number | null) {
    if (time === this.hovered) return;
    this.hovered = time;
    this.param?.requestUpdate();
  }

  paneViews(): IPrimitivePaneView[] {
    return [
      {
        zOrder: () => 'top' as const,
        renderer: () => ({
          draw: (target) => {
            const chart = this.param?.chart;
            const series = this.series;
            if (!chart || !series) return;
            target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
              ctx.save();
              ctx.font = '600 11px -apple-system, BlinkMacSystemFont, sans-serif';
              ctx.textBaseline = 'middle';
              for (const label of drawOrder(stackLabels(this.labels), this.hovered)) {
                const active = label.time === this.hovered;
                if (this.hoverOnly && !active) continue;
                const x = chart.timeScale().timeToCoordinate(label.time as Time);
                const y = series.priceToCoordinate(label.price);
                if (
                  x === null ||
                  y === null ||
                  x < 0 ||
                  x > mediaSize.width ||
                  y < 0 ||
                  y > mediaSize.height
                )
                  continue;
                let text = active ? label.text : labelText(label.text);
                while (text.length > 1 && ctx.measureText(text).width + 12 > mediaSize.width - 8) {
                  text = `${text.slice(0, -2)}…`;
                }
                const width = ctx.measureText(text).width + 12;
                const left = Math.max(4, Math.min(mediaSize.width - width - 4, x - width / 2));
                const top = label.above
                  ? y - 12 - HEIGHT - label.slot * (HEIGHT + 3)
                  : y + 12 + label.slot * (HEIGHT + 3);
                if (top < 0 || top + HEIGHT > mediaSize.height) continue;
                ctx.globalAlpha = active ? 1 : 0.45;
                ctx.fillStyle = '#ffffff';
                ctx.strokeStyle = label.color;
                ctx.lineWidth = active ? 2 : 1;
                ctx.beginPath();
                ctx.roundRect(left, top, width, HEIGHT, 4);
                ctx.fill();
                ctx.stroke();
                ctx.fillStyle = label.color;
                ctx.fillText(text, left + 6, top + HEIGHT / 2 + 0.5);
              }
              ctx.restore();
            });
          },
        }),
      },
    ];
  }
}
