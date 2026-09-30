import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactElement, ReactNode } from "react";
import uPlot from "uplot";

import { useT } from "../../i18n";
import type { Locale, T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatDecimal, formatMoment } from "../../lib/format";
import { isHttpUrl } from "../../lib/url";
import { Button } from "./Button";
import { useChartGroupCursor, useChartGroupStore } from "./chart/group";
import { JOINED_CELLS, historyBands, inferStep, isolatedIndices, markersInRange, nearestIndex, readingSpan, seriesStats, valueRange, zoomStep } from "./chart/model";
import type { ChartBand, ChartWindow, SeriesStats } from "./chart/model";
import { SECONDS_SPAN, formatChartTime, momentNeedsDate, spanNeedsDate } from "./chart/time";
import { Dialog } from "./Dialog";
import { IconButton } from "./IconButton";
import { Skeleton } from "./Skeleton";
import { STATUS, StatusGlyph } from "./StatusPill";
import type { Status } from "./StatusPill";
import { Tab, TabList, TabPanel, Tabs } from "./Tabs";
import { Tooltip } from "./Tooltip";

export type { ChartWindow } from "./chart/model";
export { ChartGroup } from "./chart/group";
export { formatChartTime, needsDateFormat } from "./chart/time";
export { markersInRange, zoomStep } from "./chart/model";

export interface ChartSeries {
  label: string;
  /** One value per timestamp; `null` is "no reading" and breaks the line, never zero. */
  values: readonly (number | null)[];
  /** The highest reading inside each cell, when a cell is a mean over several readings. */
  peaks?: readonly (number | null)[];
  /**
   * A series that counts a state (server errors per minute) is drawn in that state's colour,
   * with its glyph, never in the series family (docs/DESIGN.md, V-4).
   */
  state?: "failed" | "warning";
}

export interface ChartMarker {
  /** Unix seconds. */
  at: number;
  /**
   * Full accessible words: e.g. "Deploy 25, succeeded, Sep 25, 19:42". This is the marker's
   * accessible name everywhere it appears, and its visible text in the data view's list.
   */
  label: string;
  /** Colour and shape, the same vocabulary as everywhere else a state is drawn. */
  state: Status;
  /** A plain link target, used when there is no `renderMarker`. Only an http(s) URL is drawn
   * as a link; anything else falls back to an inert control, the same as having neither. */
  href?: string;
  /**
   * Wraps the marker's affordance in a link. Chart does not import a router, so a caller
   * that needs client-side navigation (TanStack's `<Link>`) passes this instead of `href`.
   * Spread `linkProps` onto the returned element and give it an accessible name from
   * `marker.label`. Returns one element, the marker's whole affordance.
   */
  renderMarker?: (
    marker: ChartMarker,
    children: ReactNode,
    linkProps: { className: string; style: CSSProperties },
  ) => ReactElement<Record<string, unknown>>;
}

/**
 * The page's own range selector, repeated in the enlarged chart so the range can be changed
 * without closing it. The page owns the range (usually in the URL) and re-renders the chart
 * with the new data.
 */
export interface ChartRangeSelector {
  value: string;
  control: ReactNode;
}

/** A line drawn across the plot at a value, with its words: an application's memory limit. */
export interface ChartLimit {
  value: number;
  /** "Limit 512 MB". */
  label: string;
}

/** What a chart draws: a regular grid of moments and a value per series per moment. */
export interface ChartData {
  /** Unix seconds, ascending, one per value of every series. */
  timestamps: readonly number[];
  /**
   * Up to three series: the first solid in the first series colour, the second dashed, the
   * third dotted. Colour is never the only channel: stroke and legend say it too.
   */
  series: readonly ChartSeries[];
  /**
   * The window asked for, Unix seconds: the time axis always spans exactly this, whatever
   * part of it has readings, so "the last 24 hours" with one hour of history shows 23 hours of
   * nothing. Defaults to the first and last timestamps.
   */
  domain?: ChartWindow;
  /** Seconds per cell of the grid. Defaults to the typical spacing of `timestamps`. */
  step?: number;
  /** What each cell is, in words that go inside a sentence: "1-minute averages". */
  resolution?: string;
  /** What one cell is, for the readout of one moment: "1-minute average". */
  cell?: string | null;
  /** The oldest reading there is: before it, the chart says there was no history. */
  firstSampleAt?: number | null;
}

/**
 * The enlarged chart's zoom, when the page reads each zoomed stretch again at a finer step.
 * Without it, the dialog zooms by stretching the page's own readings.
 */
export interface ChartZoom {
  /** The stretch shown; null for the whole window. */
  value: ChartWindow | null;
  onChange: (zoom: ChartWindow | null) => void;
  /** That stretch, read again; undefined until it arrives. */
  data?: ChartData | undefined;
  /** The finer read is on its way: what is on screen stays, and the dialog says so. */
  loading?: boolean;
}

export interface ChartProps extends ChartData {
  title: string;
  /** The window in words, e.g. "Last 24 hours": part of the summary and the enlarged view. */
  description?: string;
  /** Events drawn on the time axis: deploys, generically anything with a moment and a state. */
  markers?: readonly ChartMarker[];
  /** Formats a value for axis, readout, summary and table. Pass a stable function. */
  formatValue?: (value: number) => string;
  /** Fixes the value axis, e.g. [0, 100] for percentages. */
  yRange?: readonly [number, number];
  /**
   * Fits the value axis around the readings instead of starting it at zero: for a quantity
   * that barely moves far from zero (a disk 60% full), where zero would flatten it.
   */
  fit?: boolean;
  /** What the value is measured against, said beside the average: "of 16 GB". */
  ceiling?: number | null;
  /** A limit drawn across the plot when the readings come near it. */
  limit?: ChartLimit | null;
  /** Overrides the axis's clock-or-date choice, derived from the window otherwise. */
  timeFormat?: "clock" | "date";
  height?: number;
  /** The level of the chart's title in the page outline. */
  level?: 3 | 4;
  /** What the chart says when the window has no reading; "No readings in this window" otherwise. */
  empty?: string;
  /** Shown in the enlarged chart, where the page has one. */
  rangeSelector?: ChartRangeSelector;
  /** Zooming that reads the stretch again; the dialog stretches the page's readings without it. */
  zoom?: ChartZoom;
  className?: string;
}

type Tone = "ok" | "warn" | "fail" | "idle";

interface Palette {
  series: string[];
  fill: string;
  grid: string;
  axis: string;
  band: string;
  hatch: string;
  /** One resolved colour per state tone, for the marker hairlines the canvas draws itself. */
  tone: Record<Tone, string>;
}

const DASHES: (number[] | undefined)[] = [undefined, [4, 3], [1.5, 3]];
// The series family (tokens.css --viz-*), never the accent: violet means "you can act on this".
const SERIES_TOKENS = ["--viz-1", "--viz-2", "--text-muted"];
const TONE_TOKENS: Record<Tone, string> = { ok: "--ok", warn: "--warn", fail: "--fail", idle: "--idle" };
const AXIS_FONT = '11px "JetBrains Mono Variable", ui-monospace, monospace';

function seriesToken(series: Pick<ChartSeries, "state"> | undefined, index: number): string {
  if (series?.state !== undefined) return TONE_TOKENS[STATUS[series.state].tone];
  return SERIES_TOKENS[index] ?? "--text-faint";
}

/**
 * Resolves tokens to concrete colours for the canvas. Tokens are light-dark() pairs, so the
 * value depends on the colour scheme in force at the chart, not on the root.
 */
function readPalette(host: HTMLElement, series: readonly Pick<ChartSeries, "state">[]): Palette {
  const probe = document.createElement("span");
  // A transition on colour would make the read below return the previous token.
  probe.style.transition = "none";
  host.append(probe);
  const resolve = (token: string): string => {
    probe.style.color = `var(${token})`;
    return getComputedStyle(probe).color;
  };
  const colours = series.map((s, i) => resolve(seriesToken(s, i)));
  const tone = {
    ok: resolve(TONE_TOKENS.ok),
    warn: resolve(TONE_TOKENS.warn),
    fail: resolve(TONE_TOKENS.fail),
    idle: resolve(TONE_TOKENS.idle),
  };
  const palette = {
    series: colours,
    fill: "transparent",
    grid: resolve("--border"),
    axis: resolve("--text-faint"),
    band: resolve("--bg-sunken"),
    hatch: resolve("--border"),
    tone,
  };
  probe.remove();
  const first = colours[0] ?? "";
  // Only the first series has a fill (8%), and only when it is an identity, not a state.
  if (series[0]?.state === undefined && first.startsWith("rgb(")) palette.fill = first.replace("rgb(", "rgba(").replace(")", ", 0.08)");
  return palette;
}

/** The narrowest the value axis gets, in CSS pixels; also its width before the first draw. */
const VALUE_AXIS_MIN = 40;
const VALUE_AXIS_PADDING = 12;

/**
 * Width of the value axis for the labels it is about to draw. uPlot asks with the formatted
 * labels; the canvas measures them in the axis font (its pixels are device pixels).
 */
export function valueAxisSize(u: Pick<uPlot, "ctx">, values: readonly string[] | null | undefined): number {
  const longest = (values ?? []).reduce((widest, label) => (label.length > widest.length ? label : widest), "");
  if (longest === "") return VALUE_AXIS_MIN;
  u.ctx.font = AXIS_FONT;
  const width = u.ctx.measureText(longest).width;
  return Math.max(VALUE_AXIS_MIN, Math.ceil(width + VALUE_AXIS_PADDING));
}

/** The slice of a uPlot instance that overlay positioning needs, so the math is testable
 * without a real plot: a canvas-pixel plot-area box and the scales' value-to-pixel map. */
export interface MarkerPlot {
  valToPos: (value: number, scale: string, canvasPixels?: boolean) => number;
  bbox: { left: number; top: number; width: number; height: number };
}

export interface MarkerPosition {
  marker: ChartMarker;
  /** CSS pixels, relative to the chart's own root element (uPlot's target). */
  left: number;
  top: number;
}

/** Where each marker's affordance sits over the plot, from uPlot's own geometry. */
export function positionMarkers(u: MarkerPlot, markers: readonly ChartMarker[], pxRatio: number): MarkerPosition[] {
  const leftCss = u.bbox.left / pxRatio;
  const topCss = u.bbox.top / pxRatio;
  return markers.map((marker) => ({
    marker,
    left: leftCss + u.valToPos(marker.at, "x", false),
    top: topCss,
  }));
}

/** Draws a dashed hairline at each marker's x, top to bottom of the plot area. */
function drawMarkerLines(u: uPlot, markers: readonly ChartMarker[], colorOf: (state: Status) => string): void {
  if (markers.length === 0) return;
  const { ctx } = u;
  const dpr = uPlot.pxRatio || 1;
  ctx.save();
  ctx.lineWidth = Math.max(1, dpr);
  for (const marker of markers) {
    const x = u.valToPos(marker.at, "x", true);
    ctx.strokeStyle = colorOf(marker.state);
    ctx.setLineDash([4 * dpr, 3 * dpr]);
    ctx.beginPath();
    ctx.moveTo(x, u.bbox.top);
    ctx.lineTo(x, u.bbox.top + u.bbox.height);
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * Hatches the stretches with no history: a neutral ground and diagonal hairlines, never a
 * state colour. What they mean is said in words beside them, not by the drawing.
 */
function drawBands(u: uPlot, bands: readonly ChartBand[], palette: Palette): void {
  if (bands.length === 0) return;
  const { ctx, bbox } = u;
  const dpr = uPlot.pxRatio || 1;
  ctx.save();
  ctx.beginPath();
  ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height);
  ctx.clip();
  for (const band of bands) {
    const x0 = Math.max(bbox.left, u.valToPos(band.from, "x", true));
    const x1 = Math.min(bbox.left + bbox.width, u.valToPos(band.to, "x", true));
    if (x1 - x0 < 1) continue;
    ctx.fillStyle = palette.band;
    ctx.fillRect(x0, bbox.top, x1 - x0, bbox.height);
    ctx.save();
    ctx.beginPath();
    ctx.rect(x0, bbox.top, x1 - x0, bbox.height);
    ctx.clip();
    ctx.strokeStyle = palette.hatch;
    ctx.lineWidth = Math.max(1, dpr);
    const gap = 7 * dpr;
    ctx.beginPath();
    for (let x = x0 - bbox.height; x < x1; x += gap) {
      ctx.moveTo(x, bbox.top + bbox.height);
      ctx.lineTo(x + bbox.height, bbox.top);
    }
    ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
}

/**
 * The stretches the line must not cross, as uPlot clips them (canvas pixels, from the reading
 * before a hole to the one after it): runs of more than JOINED_CELLS empty cells.
 */
function holes(u: uPlot, seriesIdx: number, i0: number, i1: number): uPlot.Series.Gaps {
  const xs = u.data[0];
  const ys = u.data[seriesIdx] ?? [];
  const at = (i: number): number => Math.round(u.valToPos(xs[i] ?? 0, "x", true));
  const out: uPlot.Series.Gaps = [];
  let i = i0;
  while (i <= i1) {
    if (ys[i] !== null && ys[i] !== undefined) {
      i += 1;
      continue;
    }
    let j = i;
    while (j + 1 <= i1 && (ys[j + 1] === null || ys[j + 1] === undefined)) j += 1;
    if (j - i + 1 > JOINED_CELLS) out.push([at(i > i0 ? i - 1 : i), at(j < i1 ? j + 1 : j)]);
    i = j + 1;
  }
  return out;
}

/** A limit across the plot: a dashed hairline in the meta colour. */
function drawLimit(u: uPlot, value: number, colour: string): void {
  const y = u.valToPos(value, "y", true);
  const { ctx, bbox } = u;
  if (y < bbox.top || y > bbox.top + bbox.height) return;
  const dpr = uPlot.pxRatio || 1;
  ctx.save();
  ctx.strokeStyle = colour;
  ctx.lineWidth = Math.max(1, dpr);
  ctx.setLineDash([6 * dpr, 4 * dpr]);
  ctx.beginPath();
  ctx.moveTo(bbox.left, y);
  ctx.lineTo(bbox.left + bbox.width, y);
  ctx.stroke();
  ctx.restore();
}

/**
 * A description read on after the title: its first word lowercased when it is an ordinary word
 * ("Last hour" -> "last hour", not "CPU" or "GB"), and its closing full stop dropped, since the
 * summary puts its own.
 */
export function continuing(description: string): string {
  const text = description.trim().replace(/\.+$/, "");
  return /^\p{Lu}\p{Ll}/u.test(text) ? `${text.charAt(0).toLowerCase()}${text.slice(1)}` : text;
}

/** The moment in full, for assistive technology: "Sep 25, 2026, 14:32:05". */
function absoluteTime(seconds: number, locale: Locale): string {
  return formatMoment(new Date(seconds * 1000), locale);
}

function defaultFormat(value: number): string {
  return formatDecimal(value);
}

interface Summarised {
  title: string;
  description: string | undefined;
  resolution: string | undefined;
  series: readonly ChartSeries[];
  stats: readonly (SeriesStats | null)[];
  format: (value: number) => string;
  when: (seconds: number) => string;
  markerCount: number | undefined;
  since: string | null;
}

/** The chart in words: the accessible name of its image. */
function summarise(t: T, s: Summarised): string {
  const parts = s.series.map((series, i) => {
    const stats = s.stats[i];
    if (!stats) return t("common.chart.noDataFor", { label: series.label });
    return t("common.chart.seriesSummary", {
      label: series.label,
      latest: s.format(stats.latest),
      mean: s.format(stats.mean),
      peak: s.format(stats.peak),
      when: s.when(stats.peakAt),
    });
  });
  const head = [s.title, s.description ? continuing(s.description) : null, s.resolution ?? null].filter((part) => part !== null).join(", ");
  const sentences = [`${head}.`, `${parts.join("; ")}.`];
  if (s.since !== null) sentences.push(t("common.chart.noHistoryBeforeSentence", { time: s.since }));
  if (s.markerCount !== undefined) sentences.push(t("common.chart.markersInView", { count: s.markerCount }));
  return sentences.join(" ");
}

/** What the readout says of one moment, e.g. "14:32, CPU 12.4%, peak 31%". */
export function readoutWords(
  time: string,
  series: readonly ChartSeries[],
  index: number,
  format: (value: number) => string,
  t: T,
): string {
  const values = series.map((s) => {
    const value = s.values[index];
    if (value === null || value === undefined) return t("common.chart.readoutNone", { label: s.label });
    const peak = s.peaks?.[index];
    return peak !== null && peak !== undefined && peak > value
      ? t("common.chart.readoutPeak", { label: s.label, value: format(value), peak: format(peak) })
      : t("common.chart.readoutValue", { label: s.label, value: format(value) });
  });
  return [time, ...values].join(", ");
}

/**
 * `value`, at most once per `ms`: the newest value always lands, but a key held down does not
 * queue a sentence per sample for a screen reader to work through.
 */
export function useThrottled(value: string, ms: number): string {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    const wait = Math.max(0, last.current + ms - Date.now());
    const timer = setTimeout(() => {
      last.current = Date.now();
      setShown(value);
    }, wait);
    return () => {
      clearTimeout(timer);
    };
  }, [value, ms]);
  return shown;
}

const ANNOUNCE_EVERY_MS = 400;

function SeriesSwatch({ series, index }: { series: ChartSeries | undefined; index: number }) {
  const dash = DASHES[index];
  return (
    <svg width="16" height="8" viewBox="0 0 16 8" aria-hidden="true" className="shrink-0">
      <line
        x1="1"
        y1="4"
        x2="15"
        y2="4"
        stroke={`var(${seriesToken(series, index)})`}
        strokeWidth="2"
        strokeLinecap="round"
        {...(dash ? { strokeDasharray: dash.join(" ") } : {})}
      />
    </svg>
  );
}

/** A series' key: its line, and a state series' glyph too, so colour is never the only channel. */
function SeriesKey({ series, index }: { series: ChartSeries | undefined; index: number }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      {series?.state !== undefined ? (
        <StatusGlyph state={series.state} size={10} className={series.state === "failed" ? "text-fail" : "text-warn"} />
      ) : null}
      <SeriesSwatch series={series} index={index} />
    </span>
  );
}

const MARKER_ICON_CLASS = "flex size-6 items-center justify-center rounded-pill bg-surface hover:bg-surface-hover";
const MARKER_CHIP_CLASS =
  "flex h-7 items-center gap-1.5 rounded-pill border border-border bg-surface px-2.5 text-12 hover:bg-surface-hover";

/** A marker's affordance: `renderMarker` when given, else a plain link, else a focusable
 * (but inert) control. Every branch gets the same look and the same accessible name. */
function markerAffordance(marker: ChartMarker, children: ReactNode, className: string): ReactElement<Record<string, unknown>> {
  const style: CSSProperties = { color: `var(${TONE_TOKENS[STATUS[marker.state].tone]})` };
  if (marker.renderMarker) return marker.renderMarker(marker, children, { className, style });
  if (marker.href !== undefined && isHttpUrl(marker.href)) {
    return (
      <a href={marker.href} aria-label={marker.label} className={className} style={style}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" aria-disabled="true" aria-label={marker.label} className={className} style={style}>
      {children}
    </button>
  );
}

/** Everything the chart and its enlarged view derive from the data, once per change. */
interface Derived {
  timestamps: readonly number[];
  series: readonly ChartSeries[];
  domain: ChartWindow;
  step: number;
  resolution: string | undefined;
  cell: string | null | undefined;
  bands: ChartBand[];
  firstReading: number | null;
  /** Where history begins: the oldest sample there is, or the first reading shown. */
  historyStart: number | null;
  /** The oldest sample the store has, in any tier, when the page said. */
  firstSampleAt: number | null;
}

function derive(data: ChartData): Derived {
  const { timestamps, series } = data;
  const first = timestamps[0] ?? 0;
  const domain: ChartWindow = data.domain ?? [first, timestamps.at(-1) ?? first + 1];
  const step = data.step ?? inferStep(timestamps);
  const span = readingSpan(timestamps, series);
  const bands = historyBands(timestamps, series, domain, step);
  return {
    timestamps,
    series,
    domain,
    step,
    resolution: data.resolution,
    cell: data.cell,
    bands,
    firstReading: span?.[0] ?? null,
    historyStart: data.firstSampleAt ?? span?.[0] ?? null,
    firstSampleAt: data.firstSampleAt ?? null,
  };
}

interface Overlays {
  markers: MarkerPosition[];
  /** Labels of the hatched stretches, CSS pixels over the plot. */
  bands: { key: string; left: number; width: number; top: number; kind: ChartBand["kind"] }[];
  limit: { top: number; right: number } | null;
  /** The plot area in CSS pixels, relative to the host. */
  area: { left: number; top: number; width: number; height: number } | null;
}

const NO_OVERLAYS: Overlays = { markers: [], bands: [], limit: null, area: null };

function sameOverlays(a: Overlays, b: Overlays): boolean {
  return JSON.stringify({ ...a, markers: a.markers.map((m) => [m.marker.at, m.left, m.top]) }) ===
    JSON.stringify({ ...b, markers: b.markers.map((m) => [m.marker.at, m.left, m.top]) });
}

/** Where the readout card goes: beside the crosshair, flipped to the left near the right edge. */
export function placeCard(
  pointer: { left: number; top: number },
  area: { left: number; top: number; width: number; height: number },
  card: { width: number; height: number },
  host: { width: number; height: number },
): { x: number; y: number } {
  const gap = 12;
  const px = area.left + pointer.left;
  const py = area.top + Math.max(0, Math.min(area.height, pointer.top));
  let x = px + gap;
  if (x + card.width > host.width - 2) x = px - gap - card.width;
  x = Math.max(0, Math.min(x, host.width - card.width));
  let y = py + gap;
  if (y + card.height > area.top + area.height) y = py - gap - card.height;
  y = Math.max(0, Math.min(y, host.height - card.height));
  return { x: Math.round(x), y: Math.round(y) };
}

interface PlotProps {
  title: string;
  summary: string;
  derived: Derived;
  /** The stretch shown; the whole domain when null. */
  view: ChartWindow | null;
  markers: readonly ChartMarker[] | undefined;
  formatValue: (value: number) => string;
  yRange: readonly [number, number] | undefined;
  fit: boolean;
  limit: ChartLimit | null | undefined;
  timeFormat: "clock" | "date" | undefined;
  height: number;
  empty: string | undefined;
  /** Joins the page's chart group: a crosshair shared with the charts around it. */
  grouped: boolean;
  /** Set only where the chart zooms (the enlarged one): dragging across it selects a stretch. */
  onZoom?: (window: ChartWindow | null) => void;
}

/**
 * Everything under a chart's title: the readout row, and the plot with its hatched history,
 * markers, limit and the card that follows the cursor. Shared by the chart on the page and
 * the enlarged one, so both read, step and draw the same way.
 */
function ChartPlot({
  title,
  summary,
  derived,
  view,
  markers,
  formatValue,
  yRange,
  fit,
  limit,
  timeFormat,
  height,
  empty,
  grouped,
  onZoom,
}: PlotProps) {
  const t = useT();
  const selfId = useId();
  const hintId = useId();
  const group = useChartGroupStore();
  const shared = grouped ? group : null;
  const groupCursor = useChartGroupCursor(shared);

  const { timestamps, series, domain, step, bands, cell } = derived;
  const visible: ChartWindow = view ?? domain;
  const zoomable = onZoom !== undefined;
  const axisDate = timeFormat ? timeFormat === "date" : spanNeedsDate(visible);
  const axisSeconds = visible[1] - visible[0] <= SECONDS_SPAN;
  const readoutDate = momentNeedsDate(visible);
  const readoutSeconds = step < 60;
  const when = (seconds: number): string => formatChartTime(seconds, readoutDate, t.locale, readoutSeconds);

  const [overlays, setOverlays] = useState<Overlays>(NO_OVERLAYS);
  // The moment under this chart's own pointer or keyboard; the group's when another chart has it.
  const [own, setOwn] = useState<number | null>(null);
  const [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [spoken, setSpoken] = useState("");
  const announcement = useThrottled(spoken, ANNOUNCE_EVERY_MS);

  const hostRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const pointerRef = useRef<{ left: number; top: number } | null>(null);
  const syncingRef = useRef(false);
  const frameRef = useRef(0);

  // Read by uPlot's callbacks, which live as long as the plot: always the newest values.
  const live = useRef({ formatValue, markers: markers ?? [], bands, axisDate, axisSeconds, locale: t.locale, visible, limit, onZoom, shared, selfId, timestamps, step });
  useEffect(() => {
    live.current = { formatValue, markers: markers ?? [], bands, axisDate, axisSeconds, locale: t.locale, visible, limit, onZoom, shared, selfId, timestamps, step };
  });

  const data = useMemo<uPlot.AlignedData>(() => [Array.from(timestamps), ...series.map((s) => Array.from(s.values))], [timestamps, series]);
  const labelsKey = series.map((s) => `${s.label}|${s.state ?? ""}`).join("\u0000");
  const markersKey = (markers ?? []).map((m) => `${String(m.at)}|${m.state}|${m.label}`).join("\u0000");
  const bandsKey = bands.map((b) => `${String(b.from)}-${String(b.to)}`).join(",");

  const [dataMin, dataMax] = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of series) {
      s.values.forEach((v, i) => {
        if (v === null || !Number.isFinite(v)) return;
        lo = Math.min(lo, v);
        hi = Math.max(hi, s.peaks?.[i] ?? v, v);
      });
    }
    return Number.isFinite(lo) ? [lo, hi] : [null, null];
  }, [series]);
  // A limit far above the readings would flatten them against the axis: drawn only near it.
  const drawnLimit = limit && dataMax !== null && dataMax >= limit.value / 3 ? limit : null;
  const range = useMemo<[number, number]>(
    () => (yRange ? [yRange[0], yRange[1]] : valueRange(dataMin, dataMax, fit, drawnLimit?.value ?? null)),
    [yRange, dataMin, dataMax, fit, drawnLimit?.value],
  );
  const rangeRef = useRef(range);
  useEffect(() => {
    rangeRef.current = range;
  });

  /** Moves the card beside the pointer, without a render: the pointer moves every frame. */
  const placeCardNow = useCallback(() => {
    const card = cardRef.current;
    const host = hostRef.current;
    const plot = plotRef.current;
    const pointer = pointerRef.current;
    if (!card || !host || !plot || !pointer) return;
    const dpr = uPlot.pxRatio || 1;
    const area = { left: plot.bbox.left / dpr, top: plot.bbox.top / dpr, width: plot.bbox.width / dpr, height: plot.bbox.height / dpr };
    const { x, y } = placeCard(pointer, area, { width: card.offsetWidth, height: card.offsetHeight }, { width: host.clientWidth, height: host.clientHeight });
    card.style.transform = `translate(${String(x)}px, ${String(y)}px)`;
  }, []);

  const schedulePlace = useCallback(() => {
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(placeCardNow);
  }, [placeCardNow]);

  /** The pointer (or the keyboard) is on a cell of this chart; null when it left. */
  const onPointer = useCallback(
    (index: number | null, pointer: { left: number; top: number } | null) => {
      pointerRef.current = pointer;
      setOwn(index);
      if (index !== null) setDismissed(false);
      const { shared: store, selfId: id, timestamps: moments } = live.current;
      store?.setCursor(index === null ? null : (moments[index] ?? null), id);
      if (index !== null) schedulePlace();
    },
    [schedulePlace],
  );
  const onPointerRef = useRef(onPointer);
  useEffect(() => {
    onPointerRef.current = onPointer;
  });

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const meta = labelsKey.split("\u0000").map((key) => {
      const [label = "", state = ""] = key.split("|");
      const meaning: Pick<ChartSeries, "label" | "state"> = state === "failed" || state === "warning" ? { label, state } : { label };
      return meaning;
    });
    let palette = readPalette(host, meta);
    const colour = (index: number): string => palette.series[index] ?? palette.axis;
    const options: uPlot.Options = {
      width: Math.max(host.clientWidth, 200),
      height,
      legend: { show: false },
      cursor: {
        y: false,
        lock: true,
        drag: { x: zoomable, y: false, setScale: false },
        points: { size: 7, width: 2, fill: (_u, si) => colour(si - 1), stroke: (_u, si) => colour(si - 1) },
        bind: {
          // uPlot's own double-click resets the scale behind React's back; here it resets the
          // zoom the enlarged chart holds, and does nothing where there is none.
          dblclick: () => () => {
            live.current.onZoom?.(null);
            return null;
          },
        },
      },
      scales: {
        // The axis is the window asked for, never the span of the readings: a day with one hour
        // of history is a day with 23 hours of nothing.
        x: { time: true, range: () => [live.current.visible[0], live.current.visible[1]] },
        y: { range: () => rangeRef.current },
      },
      axes: [
        {
          stroke: () => palette.axis,
          font: AXIS_FONT,
          grid: { show: false },
          ticks: { stroke: () => palette.grid, width: 1, size: 4 },
          size: 24,
          gap: 4,
          // A date+time label is much wider than a clock: fewer, well-spaced ticks.
          space: () => (live.current.axisDate ? 96 : live.current.axisSeconds ? 72 : 56),
          values: (_u, splits) => splits.map((s) => formatChartTime(s, live.current.axisDate, live.current.locale, live.current.axisSeconds)),
        },
        {
          stroke: () => palette.axis,
          font: AXIS_FONT,
          grid: { stroke: () => palette.grid, width: 1 },
          ticks: { show: false },
          // As wide as the widest label, and in a group as wide as the widest of the group's,
          // so one moment falls on the same pixel in every chart of the grid.
          size: (u, values) => {
            const need = valueAxisSize(u, values);
            const store = live.current.shared;
            return store ? store.reportAxis(live.current.selfId, need) : need;
          },
          gap: 6,
          values: (_u, splits) => splits.map((s) => live.current.formatValue(s)),
        },
      ],
      series: [
        {},
        ...meta.map((s, index) => ({
          label: s.label,
          stroke: () => colour(index),
          width: 1.5,
          spanGaps: false,
          // One missing cell is crossed; a longer hole breaks the line (JOINED_CELLS). Counted in
          // cells, not in uPlot's rounded pixels, which at a day's width merge them all.
          gaps: (u: uPlot, seriesIdx: number, i0: number, i1: number) => holes(u, seriesIdx, i0, i1),
          // A reading with no neighbour cannot be a line: drawn as a dot, or not at all.
          points: {
            show: false,
            size: 5,
            width: 1,
            fill: () => colour(index),
            stroke: () => colour(index),
            filter: (u: uPlot, seriesIdx: number) => {
              const found = isolatedIndices((u.data[seriesIdx] ?? []) as (number | null)[]);
              return found.length > 0 ? found : null;
            },
          },
          // uPlot passes dashes straight to the canvas, which counts device pixels.
          ...(DASHES[index] ? { dash: DASHES[index].map((v) => v * (uPlot.pxRatio || 1)) } : {}),
          ...(index === 0 ? { fill: () => palette.fill } : {}),
        })),
      ],
      hooks: {
        drawClear: [
          (u: uPlot) => {
            drawBands(u, live.current.bands, palette);
          },
        ],
        // Fires after every redraw uPlot does on its own - construction, setData, setSize,
        // setScale - so this alone keeps the overlays in sync with what is on the canvas.
        draw: [
          (u: uPlot) => {
            const min = u.scales["x"]?.min;
            const max = u.scales["x"]?.max;
            const dpr = uPlot.pxRatio || 1;
            if (min === undefined || max === undefined) {
              setOverlays(NO_OVERLAYS);
              return;
            }
            const visible = markersInRange(live.current.markers, min, max);
            drawMarkerLines(u, visible, (state) => palette.tone[STATUS[state].tone]);
            const lim = live.current.limit;
            const [yMin, yMax] = rangeRef.current;
            const limitShown = lim !== null && lim !== undefined && lim.value >= yMin && lim.value <= yMax;
            if (limitShown) drawLimit(u, lim.value, palette.axis);
            const area = { left: u.bbox.left / dpr, top: u.bbox.top / dpr, width: u.bbox.width / dpr, height: u.bbox.height / dpr };
            const next: Overlays = {
              markers: positionMarkers(u, visible, dpr),
              bands: live.current.bands
                .filter((band) => band.kind !== "gap")
                .map((band) => {
                  const left = Math.max(area.left, area.left + u.valToPos(band.from, "x"));
                  const right = Math.min(area.left + area.width, area.left + u.valToPos(band.to, "x"));
                  return { key: `${band.kind}-${String(band.from)}`, left, width: right - left, top: area.top, kind: band.kind };
                })
                .filter((band) => band.width >= 96),
              limit: limitShown ? { top: area.top + u.valToPos(lim.value, "y"), right: 0 } : null,
              area,
            };
            setOverlays((current) => (sameOverlays(current, next) ? current : next));
          },
        ],
        setCursor: [
          (u: uPlot) => {
            if (syncingRef.current) return;
            const idx = u.cursor.idx;
            const left = u.cursor.left ?? -1;
            const locked = (u.cursor as { _lock?: boolean })._lock === true;
            setPinned(locked);
            if (idx === null || idx === undefined || left < 0) {
              onPointerRef.current(null, null);
              return;
            }
            onPointerRef.current(idx, { left, top: u.cursor.top ?? 0 });
          },
        ],
        setSelect: [
          (u: uPlot) => {
            const { left, width } = u.select;
            if (width < 2) return;
            u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
            const start = Math.round(u.posToVal(left, "x"));
            const end = Math.round(u.posToVal(left + width, "x"));
            // Narrower than two cells is not a stretch worth reading again.
            if (end - start < 2 * live.current.step) return;
            live.current.onZoom?.([start, end]);
          },
        ],
      },
    };
    const plot = new uPlot(options, data, host);
    plotRef.current = plot;

    const resize = new ResizeObserver(() => {
      plot.setSize({ width: Math.max(host.clientWidth, 200), height });
    });
    resize.observe(host);

    // Canvas pixels do not follow CSS: repaint when the theme changes.
    const repaint = (): void => {
      palette = readPalette(host, meta);
      plot.redraw(false);
    };
    const themeObserver = new MutationObserver(repaint);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", repaint);
    const store = live.current.shared;
    const stopAxis = store?.onAxisChange(() => {
      plot.redraw(false, true);
    });
    const id = live.current.selfId;

    return () => {
      resize.disconnect();
      themeObserver.disconnect();
      scheme.removeEventListener("change", repaint);
      stopAxis?.();
      store?.leave(id);
      cancelAnimationFrame(frameRef.current);
      plot.destroy();
      plotRef.current = null;
    };
    // Data, marker, band and zoom changes are applied below without rebuilding the plot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, labelsKey, zoomable]);

  // The canvas axis is drawn by uPlot, not React: redraw its labels in a new language.
  useEffect(() => {
    plotRef.current?.redraw(false, true);
  }, [t.locale]);

  const [viewFrom, viewTo] = visible;
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    // markersKey and bandsKey are not read here, but setData re-runs the draw hooks, which
    // read the newest markers and bands.
    plot.setData(data);
    plot.setScale("x", { min: viewFrom, max: viewTo });
  }, [data, markersKey, bandsKey, viewFrom, viewTo, range]);

  // Another chart of the group has the crosshair: mark the same moment here, card-less.
  const cursorAt = groupCursor.source !== null && groupCursor.source !== selfId ? groupCursor.at : null;
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot || pinned || groupCursor.source === selfId) return;
    syncingRef.current = true;
    if (cursorAt === null) plot.setCursor({ left: -10, top: -10 }, false);
    else plot.setCursor({ left: plot.valToPos(cursorAt, "x"), top: -10 }, false);
    syncingRef.current = false;
  }, [cursorAt, pinned, groupCursor.source, selfId]);

  const followed = cursorAt === null ? null : nearestIndex(timestamps, cursorAt);
  const shown = own ?? followed;
  const cardVisible = own !== null && !dismissed;

  // The card's first frame at a new moment: placed before it paints, not a frame late.
  useLayoutEffect(() => {
    if (cardVisible) placeCardNow();
  }, [cardVisible, own, placeCardNow]);

  // WCAG 1.4.13: the card goes away with Escape without moving the pointer or the focus; a
  // pinned card also lets go on a click anywhere else.
  useEffect(() => {
    if (!cardVisible) return;
    // Letting go of a pinned reading lets go of the moment too: the pointer may be anywhere by
    // now, and uPlot ignored its leaving while the reading was pinned.
    const unpin = (): void => {
      const plot = plotRef.current;
      setPinned(false);
      onPointerRef.current(null, null);
      if (!plot) return;
      (plot.cursor as { _lock?: boolean })._lock = false;
      plot.setCursor({ left: -10, top: -10 }, false);
    };
    const onKey = (event: globalThis.KeyboardEvent): void => {
      if (event.key !== "Escape") return;
      if (pinned) unpin();
      setDismissed(true);
    };
    const onDown = (event: PointerEvent): void => {
      if (!pinned || hostRef.current?.contains(event.target as Node)) return;
      unpin();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [cardVisible, pinned]);

  const visibleRange = useMemo(() => {
    let lo = timestamps.findIndex((at) => at >= visible[0]);
    let hi = timestamps.findLastIndex((at) => at <= visible[1]);
    if (lo === -1 || hi === -1 || hi < lo) {
      lo = 0;
      hi = timestamps.length - 1;
    }
    return hi < 0 ? null : ([lo, hi] as const);
  }, [timestamps, visible]);

  /** Moves the cursor to a cell from the keyboard (null hides it) and says so. */
  const moveCursor = (index: number | null, pin = false): void => {
    const plot = plotRef.current;
    if (index === null) {
      setSpoken("");
      if (plot) {
        (plot.cursor as { _lock?: boolean })._lock = false;
        plot.setCursor({ left: -10, top: -10 });
      }
      setPinned(false);
      return;
    }
    const at = timestamps[index];
    if (at === undefined) return;
    setSpoken(readoutWords(when(at), series, index, formatValue, t));
    if (plot) {
      (plot.cursor as { _lock?: boolean })._lock = false;
      const value = series[0]?.values[index];
      const top = value === null || value === undefined ? plot.bbox.height / (2 * (uPlot.pxRatio || 1)) : plot.valToPos(value, "y");
      plot.setCursor({ left: plot.valToPos(at, "x"), top });
      if (pin) {
        (plot.cursor as { _lock?: boolean })._lock = true;
        setPinned(true);
      }
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (visibleRange === null) return;
    const [lo, hi] = visibleRange;
    const current = own !== null && own >= lo && own <= hi ? own : null;
    let next: number | null;
    let pin = false;
    switch (event.key) {
      case "ArrowLeft":
        next = current === null ? hi : Math.max(lo, current - 1);
        break;
      case "ArrowRight":
        next = current === null ? hi : Math.min(hi, current + 1);
        break;
      case "PageUp":
        next = current === null ? hi : Math.max(lo, current - 10);
        break;
      case "PageDown":
        next = current === null ? hi : Math.min(hi, current + 10);
        break;
      case "Home":
        next = lo;
        break;
      case "End":
        next = hi;
        break;
      case "Enter":
      case " ":
        next = current ?? hi;
        pin = true;
        break;
      case "Escape":
        // With nothing to clear, Escape is the enclosing dialog's to close.
        if (own === null) return;
        event.stopPropagation();
        next = null;
        break;
      default:
        return;
    }
    event.preventDefault();
    moveCursor(next, pin);
  };

  const shownAt = shown === null ? undefined : timestamps[shown];
  const latest = series.map((s) => {
    for (let i = s.values.length - 1; i >= 0; i -= 1) {
      const value = s.values[i];
      if (value !== null && value !== undefined) return value;
    }
    return null;
  });
  const readout = series.map((s, index) => (shown === null ? latest[index] : s.values[shown]) ?? null);
  const hasReading = dataMin !== null;
  const cardIndex = own;
  const cardAt = cardIndex === null ? undefined : timestamps[cardIndex];
  const cardEmpty = cardIndex !== null && series.every((s) => s.values[cardIndex] === null || s.values[cardIndex] === undefined);

  /** The words on a hatched stretch: before history began, or a hole in it, or since it stopped. */
  const bandWords = (kind: ChartBand["kind"]): string | null => {
    const first = derived.firstReading;
    if (kind === "after") return t("common.chart.noReadingsAfter");
    if (kind !== "before" || first === null) return null;
    // History began earlier than this stretch: the empty start is a hole, not the beginning.
    const hole = derived.firstSampleAt !== null && derived.firstSampleAt < first - step;
    // As short as the axis writes it: the band sits on that axis.
    const time = formatChartTime(first, axisDate, t.locale);
    return hole ? t("common.chart.noReadingsBefore", { time }) : t("common.chart.noHistoryBefore", { time });
  };

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex h-4 min-w-0 items-center gap-x-4 overflow-hidden text-12 whitespace-nowrap">
        {/* The moment the values beside it were read: the one under the cursor, or the newest. */}
        <span className="mono shrink-0 text-fg-muted" data-readout-time="">
          {shownAt === undefined ? (
            t("common.chart.latest")
          ) : (
            <time dateTime={new Date(shownAt * 1000).toISOString()}>
              <span aria-hidden="true">{when(shownAt)}</span>
              <span className="sr-only">{absoluteTime(shownAt, t.locale)}</span>
            </time>
          )}
        </span>
        <ul className="flex min-w-0 gap-x-4" aria-label={t("common.chart.seriesLabel")}>
          {series.map((s, index) => {
            const value = readout[index];
            return (
              <li key={s.label} className="flex min-w-0 items-center gap-1.5 text-fg-muted">
                <SeriesKey series={s} index={index} />
                <span className="truncate">{s.label}</span>
                <span className="mono text-fg tabular-nums">{value !== null && value !== undefined ? formatValue(value) : "–"}</span>
              </li>
            );
          })}
        </ul>
      </div>
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>

      <div className="relative min-w-0">
        {/* The keyboard's way into the readout. An application, not the image itself: a
            screen reader in browse mode keeps the arrow keys for itself on anything else,
            and they are what steps from sample to sample here. */}
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
        <div
          role="application"
          aria-roledescription={t("common.chart.roleDescription")}
          aria-label={title}
          aria-describedby={hintId}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          onKeyDown={onKeyDown}
          onBlur={(event) => {
            if (event.currentTarget.contains(event.relatedTarget)) return;
            if (spoken !== "") moveCursor(null);
          }}
          className="rounded-control"
        >
          <div
            ref={hostRef}
            role="img"
            aria-label={summary}
            className="relative min-w-0"
            data-domain-from={visible[0]}
            data-domain-to={visible[1]}
            style={{ height }}
          />
        </div>
        <span id={hintId} className="sr-only">
          {t("common.chart.keyboardHint")}
          {zoomable ? t("common.chart.keyboardHintZoomable") : ""}
        </span>

        {/* Words for the hatched stretches: text a person and a screen reader can read. */}
        {hasReading
          ? overlays.bands.map((band) => (
              <span
                key={band.key}
                aria-hidden="true"
                className="pointer-events-none absolute truncate px-2 pt-1.5 text-12 text-fg-faint"
                style={{ left: band.left, top: band.top, width: band.width }}
              >
                {bandWords(band.kind)}
              </span>
            ))
          : null}
        {overlays.limit !== null && drawnLimit ? (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute -translate-y-full px-2 pb-0.5 text-12 text-fg-faint"
            style={{ top: overlays.limit.top, left: overlays.area?.left ?? 0 }}
          >
            {drawnLimit.label}
          </span>
        ) : null}
        {!hasReading && overlays.area !== null ? (
          <p
            className="pointer-events-none absolute flex items-center justify-center px-4 text-center text-13 text-pretty text-fg-muted"
            style={{ left: overlays.area.left, top: overlays.area.top, width: overlays.area.width, height: overlays.area.height }}
          >
            {empty ?? t("common.chart.noReadings")}
          </p>
        ) : null}

        {overlays.markers.map(({ marker, left, top }) => (
          <div
            key={`${String(marker.at)}-${marker.label}`}
            // Centred on the hairline and on the plot's top edge.
            className="absolute -translate-x-1/2 -translate-y-1/2"
            style={{ left, top }}
          >
            <Tooltip content={marker.label}>
              {markerAffordance(marker, <StatusGlyph state={marker.state} size={10} />, MARKER_ICON_CLASS)}
            </Tooltip>
          </div>
        ))}

        {/* The reading beside the cursor. Its values are also in the row above, which does not
            depend on the pointer, so the card is a convenience and hidden from assistive
            technology; pinned (a click, Enter), it stays and its text can be selected. */}
        <div
          ref={cardRef}
          aria-hidden="true"
          data-chart-card={pinned ? "pinned" : cardVisible ? "floating" : "hidden"}
          className={cx(
            "absolute top-0 left-0 flex min-w-40 flex-col gap-1.5 rounded-control border border-border bg-surface-raised px-3 py-2 text-12 shadow-overlay",
            pinned ? "select-text" : "pointer-events-none",
            !cardVisible && "invisible",
          )}
        >
          {cardAt !== undefined ? (
            <>
              <div className="flex items-baseline justify-between gap-4">
                <span className="mono text-fg">{when(cardAt)}</span>
                {cell ? <span className="text-fg-faint">{cell}</span> : null}
              </div>
              {cardEmpty ? (
                <p className="max-w-56 text-pretty text-fg-muted">
                  {derived.historyStart === null || cardAt < derived.historyStart ? t("common.chart.cardNoHistory") : t("common.chart.cardNoReading")}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {series.map((s, index) => {
                    const value = cardIndex === null ? null : s.values[cardIndex];
                    const peak = cardIndex === null ? null : s.peaks?.[cardIndex];
                    return (
                      <li key={s.label} className="flex flex-col">
                        <span className="flex items-center gap-1.5">
                          <SeriesKey series={s} index={index} />
                          <span className="text-fg-muted">{s.label}</span>
                          <span className="mono ml-auto pl-3 text-fg tabular-nums">
                            {value === null || value === undefined ? t("common.chart.noReading") : formatValue(value)}
                          </span>
                        </span>
                        {peak !== null && peak !== undefined && value !== null && value !== undefined && peak > value ? (
                          <span className="flex justify-between gap-3 pl-5 text-fg-faint">
                            <span>{t("common.chart.peak")}</span>
                            <span className="mono tabular-nums">{formatValue(peak)}</span>
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
              {pinned ? <span className="text-fg-faint">{t("common.chart.pinnedHint")}</span> : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * The same readings as a table: newest first, with the time to the second when readings are
 * seconds apart, and each cell's peak beside its average.
 */
function ChartTable({ title, derived, view, formatValue, markers }: { title: string; derived: Derived; view: ChartWindow | null; formatValue: (value: number) => string; markers: readonly ChartMarker[] }) {
  const t = useT();
  const { timestamps, series, step } = derived;
  const shown = view ?? derived.domain;
  const withDate = momentNeedsDate(shown);
  const withSeconds = step < 60;
  const withPeaks = series.map((s) => s.peaks?.some((p, i) => p !== null && p !== (s.values[i] ?? null)) === true);
  const rows = timestamps
    .map((moment, i) => ({ moment, i }))
    .filter(({ moment }) => moment >= shown[0] && moment <= shown[1])
    .reverse();
  const stats = series.map((s) => seriesStats(timestamps, s.values, s.peaks, shown));
  const visibleMarkers = markersInRange(markers, shown[0], shown[1]);
  const regionLabel = t("common.chart.dataRegion", { title });
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <dl className="flex shrink-0 flex-col gap-1.5 text-12">
        {series.map((s, index) => {
          const stat = stats[index];
          return (
            <div key={s.label} className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <dt className="flex items-center gap-1.5 font-medium text-fg">
                <SeriesKey series={s} index={index} />
                {s.label}
              </dt>
              {stat ? (
                <>
                  <dd className="text-fg-muted">
                    {t("common.chart.statLow")} <span className="mono text-fg tabular-nums">{formatValue(stat.low)}</span>
                  </dd>
                  <dd className="text-fg-muted">
                    {t("common.chart.statMean")} <span className="mono text-fg tabular-nums">{formatValue(stat.mean)}</span>
                  </dd>
                  <dd className="text-fg-muted">
                    {t("common.chart.statPeak")} <span className="mono text-fg tabular-nums">{formatValue(stat.peak)}</span>
                  </dd>
                  <dd className="text-fg-muted">
                    {t("common.chart.statLatest")} <span className="mono text-fg tabular-nums">{formatValue(stat.latest)}</span>
                  </dd>
                </>
              ) : (
                <dd className="text-fg-muted">{t("common.chart.noReadings")}</dd>
              )}
            </div>
          );
        })}
      </dl>
      <div
        role="region"
        aria-label={regionLabel}
        tabIndex={0}
        className="min-h-0 flex-1 overflow-auto rounded-control border border-border scroll-thin -outline-offset-2"
      >
        <table className="w-full text-left text-12">
          <caption className="sr-only">{t("common.chart.newestFirst", { title })}</caption>
          <thead className="sticky top-0 bg-bg-sunken">
            <tr>
              <th scope="col" className="px-3 py-1.5 font-medium text-fg-muted">
                {t("common.chart.time")}
              </th>
              {series.flatMap((s, index) => [
                <th key={s.label} scope="col" className="px-3 py-1.5 text-right font-medium text-fg-muted">
                  {s.label}
                </th>,
                ...(withPeaks[index]
                  ? [
                      <th key={`${s.label}-peak`} scope="col" className="px-3 py-1.5 text-right font-medium text-fg-muted">
                        {t("common.chart.peakOf", { label: s.label })}
                      </th>,
                    ]
                  : []),
              ])}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ moment, i }) => (
              <tr key={moment} className="border-t border-border">
                <td className="mono px-3 py-1 whitespace-nowrap text-fg-muted">{formatChartTime(moment, withDate, t.locale, withSeconds)}</td>
                {series.flatMap((s, index) => {
                  const value = s.values[i];
                  const peak = s.peaks?.[i];
                  return [
                    <td key={s.label} className="mono px-3 py-1 text-right text-fg tabular-nums">
                      {value === null || value === undefined ? <span className="text-fg-faint">{t("common.chart.noReading")}</span> : formatValue(value)}
                    </td>,
                    ...(withPeaks[index]
                      ? [
                          <td key={`${s.label}-peak`} className="mono px-3 py-1 text-right text-fg-muted tabular-nums">
                            {peak === null || peak === undefined ? "–" : formatValue(peak)}
                          </td>,
                        ]
                      : []),
                  ];
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {visibleMarkers.length > 0 ? (
          <div className="border-t border-border p-3">
            <p className="mb-2 text-12 font-medium text-fg-muted">{t("common.chart.markersHeading")}</p>
            <ul className="flex flex-wrap gap-2">
              {visibleMarkers.map((marker) => (
                <li key={`${String(marker.at)}-${marker.label}`}>
                  {markerAffordance(
                    marker,
                    <>
                      <StatusGlyph state={marker.state} size={10} />
                      <span className="text-fg">{marker.label}</span>
                    </>,
                    MARKER_CHIP_CLASS,
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A dialog's chart fills what the viewport leaves under the dialog's header and controls. */
function expandedHeight(): number {
  return Math.round(Math.min(520, Math.max(220, window.innerHeight * 0.88 - 340)));
}

/** "Showing Sep 28 21:40 to Sep 29 21:40, 1-minute averages. History since 20:12." */
export function truthLine(t: T, shown: ChartWindow, resolution: string | undefined, firstSampleAt: number | null | undefined): string {
  const withDate = momentNeedsDate(shown);
  const withSeconds = shown[1] - shown[0] <= SECONDS_SPAN;
  const from = formatChartTime(shown[0], withDate, t.locale, withSeconds);
  const to = formatChartTime(shown[1], withDate, t.locale, withSeconds);
  const since =
    firstSampleAt !== null && firstSampleAt !== undefined && firstSampleAt > shown[0]
      ? formatChartTime(firstSampleAt, momentNeedsDate([firstSampleAt, shown[1]]), t.locale)
      : null;
  if (resolution === undefined) return t("common.chart.showingBare", { from, to });
  return since === null ? t("common.chart.showing", { from, to, resolution }) : t("common.chart.showingSince", { from, to, resolution, since });
}

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string | undefined;
  derived: Derived;
  plot: Omit<PlotProps, "summary" | "derived" | "view" | "height" | "grouped" | "onZoom" | "title">;
  rangeSelector: ChartRangeSelector | undefined;
  zoom: ChartZoom | undefined;
  summaryOf: (derived: Derived, view: ChartWindow | null) => string;
}

/** The chart enlarged: the page's range, a zoom that reads again, the readout and the data. */
function ChartDialog({ open, onOpenChange, title, description, derived, plot, rangeSelector, zoom, summaryOf }: DialogProps) {
  const t = useT();
  const [tab, setTab] = useState<"chart" | "data">("chart");
  // Without a page that reads each stretch again, the zoom stretches what is here, and it
  // belongs to the range it was made in: a new range starts whole.
  const [local, setLocal] = useState<{ range: string; window: ChartWindow } | null>(null);
  const rangeKey = rangeSelector?.value ?? "";
  const [height] = useState(expandedHeight);

  const active = zoom ? zoom.value : local !== null && local.range === rangeKey ? local.window : null;
  const apply = (window: ChartWindow | null): void => {
    if (zoom) zoom.onChange(window);
    else setLocal(window === null ? null : { range: rangeKey, window });
  };
  const zoomed = zoom && active !== null && zoom.data ? derive(zoom.data) : null;
  const shownData = zoomed ?? derived;
  const view = zoomed ? null : active;
  const shownWindow = view ?? shownData.domain;
  const minSpan = Math.max(8 * derived.step, 60);
  const truth = truthLine(t, shownWindow, shownData.resolution, shownData.historyStart);
  const reading = zoom?.loading === true && active !== null;

  // The chart panel and the data panel are the same height, so switching never moves the dialog.
  const panelHeight = 16 + 12 + height;

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description} size="xl">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>{rangeSelector?.control}</div>
          <div className="flex flex-wrap items-center gap-1">
            <IconButton
              label={t("common.chart.zoomIn")}
              icon={<ZoomIn />}
              size="sm"
              onClick={() => {
                apply(zoomStep(derived.domain, active, "in", minSpan));
              }}
            />
            <IconButton
              label={t("common.chart.zoomOut")}
              icon={<ZoomOut />}
              size="sm"
              disabled={active === null}
              onClick={() => {
                apply(zoomStep(derived.domain, active, "out", minSpan));
              }}
            />
            <Button
              size="sm"
              variant="ghost"
              disabled={active === null}
              onClick={() => {
                apply(null);
              }}
            >
              {t("common.chart.resetZoom")}
            </Button>
          </div>
        </div>
        <p className="min-h-5 text-13 text-pretty text-fg-muted" aria-live="polite" data-truth="">
          {reading ? t("common.chart.readingAgain") : truth}
        </p>
        <Tabs value={tab} onValueChange={(next: "chart" | "data") => setTab(next)}>
          <TabList aria-label={t("common.chart.viewsLabel", { title })}>
            <Tab value="chart">{t("common.chart.chartTab")}</Tab>
            <Tab value="data">{t("common.chart.dataTab")}</Tab>
          </TabList>
          <TabPanel value="chart" className="pt-4">
            <div style={{ height: panelHeight }}>
              <ChartPlot
                {...plot}
                title={title}
                summary={summaryOf(shownData, view)}
                derived={shownData}
                view={view}
                height={height}
                grouped={false}
                onZoom={apply}
              />
            </div>
          </TabPanel>
          <TabPanel value="data" className="pt-4">
            <div style={{ height: panelHeight }}>
              <ChartTable title={title} derived={shownData} view={view} formatValue={plot.formatValue} markers={plot.markers ?? []} />
            </div>
          </TabPanel>
        </Tabs>
        <p className="text-12 text-pretty text-fg-faint">{active === null ? t("common.chart.dragToZoom") : t("common.chart.zoomedHint")}</p>
      </div>
    </Dialog>
  );
}

/** The chart's frame before its data arrives: exactly as tall as the chart that replaces it. */
export function ChartSkeleton({ title, height = 160 }: { title: string; height?: number }) {
  const t = useT();
  return (
    <div aria-busy="true" className="flex min-w-0 flex-col gap-3">
      <span className="sr-only">{t("common.chart.loading", { title })}</span>
      <div aria-hidden="true" className="flex h-9 flex-col justify-center gap-1.5">
        <Skeleton className="h-3.5 w-20" />
        <Skeleton className="h-3 w-40 max-w-full" />
      </div>
      <div aria-hidden="true" className="flex h-4 items-center">
        <Skeleton className="h-3 w-28" />
      </div>
      <div aria-hidden="true" className="w-full rounded-control bg-bg-sunken" style={{ height }} />
    </div>
  );
}

/**
 * A chart's frame holding a message instead of a plot (a read that failed): the same height as
 * the chart it stands for, so the charts around it and the page below do not move.
 */
export function ChartMessage({ title, height = 160, children }: { title: string; height?: number; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex h-9 items-center">
        <p className="truncate text-13 font-medium text-fg">{title}</p>
      </div>
      <div className="h-4" aria-hidden="true" />
      <div className="flex min-w-0 items-center justify-center overflow-hidden" style={{ height }}>
        {children}
      </div>
    </div>
  );
}

/** One line under the title: the average and the peak, or which peaks, over what is shown. */
function statsLine(t: T, series: readonly ChartSeries[], stats: readonly (SeriesStats | null)[], format: (value: number) => string, when: (s: number) => string, ceiling: number | null | undefined): string {
  const present = stats.filter((s): s is SeriesStats => s !== null);
  if (present.length === 0) return t("common.chart.noReadings");
  const first = stats[0];
  if (series.length === 1 && first) {
    const values = { mean: format(first.mean), peak: format(first.peak), when: when(first.peakAt) };
    return ceiling !== null && ceiling !== undefined
      ? t("common.chart.statsOf", { ...values, ceiling: format(ceiling) })
      : t("common.chart.stats", values);
  }
  const peaks = series
    .map((s, i) => {
      const stat = stats[i];
      return stat ? t("common.chart.peakValue", { label: s.label, value: format(stat.peak) }) : null;
    })
    .filter((part): part is string => part !== null);
  return t("common.chart.peaks", { values: peaks.join(" · ") });
}

/**
 * A time series drawn with uPlot, over exactly the window asked for: where nothing was recorded
 * the line breaks and the stretch is hatched and named. The canvas is an image with a written
 * summary; hovering or stepping with the arrow keys reads one moment beside the cursor and in
 * the row above it, and every chart of a ChartGroup marks the same moment. Expand opens it large,
 * with the page's range, a zoom that reads the stretch again, and the numbers as a table.
 */
export function Chart({
  title,
  description,
  timestamps,
  series,
  domain,
  step,
  resolution,
  cell,
  firstSampleAt,
  markers,
  formatValue = defaultFormat,
  yRange,
  fit = false,
  ceiling,
  limit,
  timeFormat,
  height = 160,
  level = 3,
  empty,
  rangeSelector,
  zoom,
  className,
}: ChartProps) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);

  const derived = useMemo(
    () =>
      derive({
        timestamps,
        series,
        ...(domain !== undefined ? { domain } : {}),
        ...(step !== undefined ? { step } : {}),
        ...(resolution !== undefined ? { resolution } : {}),
        ...(cell !== undefined ? { cell } : {}),
        ...(firstSampleAt !== undefined ? { firstSampleAt } : {}),
      }),
    [timestamps, series, domain, step, resolution, cell, firstSampleAt],
  );

  const summaryOf = (data: Derived, view: ChartWindow | null): string => {
    const shown = view ?? data.domain;
    const readoutDate = momentNeedsDate(shown);
    const when = (seconds: number): string => formatChartTime(seconds, readoutDate, t.locale, data.step < 60);
    const stats = data.series.map((s) => seriesStats(data.timestamps, s.values, s.peaks, shown));
    const start = data.historyStart;
    const since = start !== null && start > shown[0] + data.step ? when(start) : null;
    return summarise(t, {
      title,
      description,
      resolution: data.resolution,
      series: data.series,
      stats,
      format: formatValue,
      when,
      markerCount: markers !== undefined ? markersInRange(markers, shown[0], shown[1]).length : undefined,
      since,
    });
  };

  const stats = useMemo(() => derived.series.map((s) => seriesStats(derived.timestamps, s.values, s.peaks, derived.domain)), [derived]);
  // As short as the axis writes it: the line has the width of one chart.
  const axisDate = timeFormat ? timeFormat === "date" : spanNeedsDate(derived.domain);
  const when = (seconds: number): string => formatChartTime(seconds, axisDate, t.locale);
  const line = statsLine(t, derived.series, stats, formatValue, when, ceiling);
  const Heading = `h${String(level)}` as "h3" | "h4";

  const plot = { markers, formatValue, yRange, fit, limit, timeFormat, empty };

  return (
    <figure className={cx("flex min-w-0 flex-col gap-3", className)}>
      <figcaption className="flex h-9 items-start justify-between gap-3">
        <div className="min-w-0">
          <Heading className="truncate text-13 font-medium text-fg">{title}</Heading>
          <p className="truncate text-12 text-fg-faint" title={line} data-chart-stats="">
            {line}
          </p>
        </div>
        <IconButton
          label={t("common.chart.expand", { title })}
          icon={<Maximize2 />}
          size="sm"
          className="-mr-1.5"
          onClick={() => {
            setExpanded(true);
          }}
        />
      </figcaption>

      <ChartPlot {...plot} title={title} summary={summaryOf(derived, null)} derived={derived} view={null} height={height} grouped />

      {expanded ? (
        <ChartDialog
          open={expanded}
          onOpenChange={(open) => {
            setExpanded(open);
            if (!open) zoom?.onChange(null);
          }}
          title={title}
          description={description}
          derived={derived}
          plot={plot}
          rangeSelector={rangeSelector}
          zoom={zoom}
          summaryOf={summaryOf}
        />
      ) : null}
    </figure>
  );
}
