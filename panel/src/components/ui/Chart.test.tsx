import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { bindT } from "../../i18n";
import { expectNoAxeViolations } from "../../test/axe";
import type { ChartData, ChartMarker, ChartProps, ChartWindow, MarkerPlot } from "./Chart";
import { Chart, ChartGroup, ChartSkeleton, continuing, placeCard, positionMarkers, readoutWords, truthLine, valueAxisSize } from "./Chart";
import { formatChartTime } from "./chart/time";

// uPlot draws on a canvas, which jsdom does not implement; the chart's contract is the
// accessible summary, the readout row and card, the hatched history's words, the markers, the
// group's shared moment, the dialog and the lifecycle of the plot, all testable without pixels.
// The fake plot implements just enough of uPlot: its geometry (`bbox`, `valToPos`, `posToVal`),
// the x scale taken from the chart's own `range` function, and its hooks.
interface FakePlot {
  options: {
    scales: { x: { range: () => [number, number] } };
    series: { gaps?: (u: unknown, i: number, i0: number, i1: number) => [number, number][] }[];
    axes: { size?: (u: unknown, values: string[]) => number; values?: (u: unknown, splits: number[]) => string[] }[];
  };
  data: (number | null)[][];
  scales: { x: { min?: number; max?: number }; y: { min?: number; max?: number } };
  cursor: { idx: number | null; left: number; top: number; _lock?: boolean };
  select: { left: number; top: number; width: number; height: number };
  bbox: { left: number; top: number; width: number; height: number };
  destroy: () => void;
  setData: ReturnType<typeof vi.fn>;
  setScale: ReturnType<typeof vi.fn>;
  setCursor: ReturnType<typeof vi.fn>;
  setSelect: ReturnType<typeof vi.fn>;
  redraw: ReturnType<typeof vi.fn>;
  valToPos: (val: number, scale?: string, canvas?: boolean) => number;
  posToVal: (pos: number, scale?: string) => number;
  fire: (hook: "draw" | "setCursor" | "setSelect" | "drawClear") => void;
}

const plots = vi.hoisted(() => [] as FakePlot[]);

vi.mock("uplot", () => {
  const bbox = { left: 40, top: 8, width: 400, height: 144 };

  function build(options: Record<string, unknown>, initialData: unknown) {
    let current = initialData as number[][];
    const hooks = (options["hooks"] ?? {}) as Record<string, ((u: unknown) => void)[] | undefined>;
    const scaleOptions = options["scales"] as { x: { range: () => [number, number] }; y: { range: () => [number, number] } };
    const scales = { x: {} as { min?: number; max?: number }, y: {} as { min?: number; max?: number } };
    const rescale = (): void => {
      const [min, max] = scaleOptions.x.range();
      scales.x = { min, max };
      const [ymin, ymax] = scaleOptions.y.range();
      scales.y = { min: ymin, max: ymax };
    };
    const valToPos = (val: number, scale = "x"): number => {
      const s = scale === "y" ? scales.y : scales.x;
      const min = s.min ?? 0;
      const max = s.max ?? min + 1;
      const frac = max > min ? (val - min) / (max - min) : 0;
      return scale === "y" ? bbox.height - frac * bbox.height : frac * bbox.width;
    };
    const posToVal = (pos: number): number => {
      const min = scales.x.min ?? 0;
      const max = scales.x.max ?? min + 1;
      return min + (pos / bbox.width) * (max - min);
    };
    const ctx = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      fillRect: vi.fn(),
      setLineDash: vi.fn(),
      measureText: (text: string) => ({ width: text.length * 7 }),
      font: "",
    };
    const fire = (hook: string): void => {
      (hooks[hook] ?? []).forEach((fn) => {
        fn(plot);
      });
    };
    const plot = {
      options,
      data: current,
      bbox,
      ctx,
      scales,
      cursor: { idx: null as number | null, left: -10, top: -10 },
      select: { left: 0, top: 0, width: 0, height: 0 },
      valToPos,
      posToVal,
      destroy: vi.fn(),
      setSize: vi.fn(),
      redraw: vi.fn(),
      setCursor: vi.fn((opts: { left: number; top: number }, fireHook?: boolean) => {
        plot.cursor.left = opts.left;
        plot.cursor.top = opts.top;
        const times = current[0] ?? [];
        if (opts.left < 0) plot.cursor.idx = null;
        else {
          const at = posToVal(opts.left);
          let best = 0;
          times.forEach((t, i) => {
            if (Math.abs(t - at) < Math.abs((times[best] ?? 0) - at)) best = i;
          });
          plot.cursor.idx = best;
        }
        if (fireHook !== false) fire("setCursor");
      }),
      setSelect: vi.fn(),
      setScale: vi.fn(() => {
        rescale();
        fire("drawClear");
        fire("draw");
      }),
      setData: vi.fn((next: unknown) => {
        current = next as number[][];
        plot.data = current;
        rescale();
        fire("drawClear");
        fire("draw");
      }),
      fire,
    };
    plots.push(plot as unknown as FakePlot);
    rescale();
    fire("drawClear");
    fire("draw");
    return plot;
  }

  const ctor = vi.fn(function (this: unknown, options: Record<string, unknown>, data: unknown) {
    return build(options, data);
  }) as unknown as { new (...args: unknown[]): unknown; pxRatio: number };
  ctor.pxRatio = 1;
  return { default: ctor };
});

const T0 = new Date(2026, 8, 25, 14, 0).getTime() / 1000;
const TIMES = [T0, T0 + 60, T0 + 120];
const percent = (v: number): string => `${String(v)}%`;

beforeEach(() => {
  plots.length = 0;
});

function Example(props: Partial<ChartProps>) {
  return (
    <Chart
      title="CPU"
      description="Last 3 minutes"
      timestamps={TIMES}
      series={[{ label: "shop.example.com", values: [12, 48, 30] }]}
      formatValue={percent}
      yRange={[0, 100]}
      resolution="1-minute averages"
      cell="1-minute average"
      step={60}
      {...props}
    />
  );
}

/** The plot on the page: the first one built (the enlarged chart builds its own). */
function pagePlot(): FakePlot {
  const plot = plots[0];
  if (plot === undefined) throw new Error("no plot was built");
  return plot;
}

/** Puts uPlot's cursor on a cell (null: the pointer left) and fires its hook, as a mouse does. */
function hover(plot: FakePlot, idx: number | null): void {
  act(() => {
    plot.cursor.idx = idx;
    plot.cursor.left = idx === null ? -10 : 100;
    plot.cursor.top = 40;
    plot.fire("setCursor");
  });
}

function readoutTime(scope: HTMLElement = document.body): HTMLElement {
  const time = scope.querySelector<HTMLElement>("[data-readout-time]");
  if (time === null) throw new Error("the readout has no time");
  return time;
}

function card(scope: HTMLElement = document.body): HTMLElement {
  const found = scope.querySelector<HTMLElement>("[data-chart-card]");
  if (found === null) throw new Error("no readout card");
  return found;
}

describe("Chart", () => {
  it("is an image with a written summary: the window, what a point is, and each series", () => {
    render(<Example />);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      `CPU, last 3 minutes, 1-minute averages. shop.example.com: latest 30%, average 30%, peak 48% at ${formatChartTime(T0 + 60, false)}.`,
    );
  });

  it("says the average and the peak under its title, and what it is measured against", () => {
    render(<Example ceiling={100} />);
    expect(document.querySelector("[data-chart-stats]")).toHaveTextContent(`Average 30% of 100% · peak 48% at ${formatChartTime(T0 + 60, false)}`);
  });

  it("draws the data with uPlot and destroys the plot when it goes", () => {
    const { unmount } = render(<Example />);
    const plot = pagePlot();
    expect(plot.data).toEqual([TIMES, [12, 48, 30]]);
    unmount();
    expect(plot.destroy).toHaveBeenCalled();
  });

  it("spans exactly the window asked for, whatever part of it has readings", () => {
    // A day asked for, with readings only in its last three minutes.
    const domain: ChartWindow = [T0 - 86_400 + 120, T0 + 120];
    render(<Example domain={domain} />);
    const plot = pagePlot();
    expect(plot.options.scales.x.range()).toEqual([domain[0], domain[1]]);
    expect(plot.scales.x).toEqual({ min: domain[0], max: domain[1] });
    const image = screen.getByRole("img");
    expect(image).toHaveAttribute("data-domain-from", String(domain[0]));
    expect(image).toHaveAttribute("data-domain-to", String(domain[1]));
  });

  it("keeps the window after a refresh with new readings", () => {
    const domain: ChartWindow = [T0 - 3_600, T0 + 120];
    const { rerender } = render(<Example domain={domain} />);
    rerender(<Example domain={domain} series={[{ label: "shop.example.com", values: [12, 48, 31] }]} />);
    expect(pagePlot().scales.x).toEqual({ min: domain[0], max: domain[1] });
  });

  it("names the stretch before history began, on the plot and in its summary", () => {
    const times = Array.from({ length: 60 }, (_, i) => T0 + i * 60);
    const values = times.map((_, i) => (i >= 50 ? 10 + i : null));
    render(<Example timestamps={times} series={[{ label: "CPU", values }]} domain={[T0, T0 + 59 * 60]} firstSampleAt={T0 + 50 * 60} />);
    const since = formatChartTime(T0 + 50 * 60, false);
    expect(screen.getByText(`No history before ${since}`)).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAccessibleName(new RegExp(`No history before ${since}\\.$`));
  });

  it("says a hole is a hole when history began before it", () => {
    const times = Array.from({ length: 60 }, (_, i) => T0 + i * 60);
    const values = times.map((_, i) => (i >= 50 ? 5 : null));
    render(<Example timestamps={times} series={[{ label: "CPU", values }]} domain={[T0, T0 + 59 * 60]} firstSampleAt={T0 - 86_400} />);
    expect(screen.getByText(`No readings before ${formatChartTime(T0 + 50 * 60, false)}`)).toBeInTheDocument();
  });

  it("says when the window has no reading at all, in the plot's place and height", () => {
    render(<Example series={[{ label: "CPU", values: [null, null, null] }]} empty="Noust was not recording then." />);
    expect(screen.getByText("Noust was not recording then.")).toBeInTheDocument();
    expect(document.querySelector("[data-chart-stats]")).toHaveTextContent("No readings in this window");
  });

  it("breaks the line only at a hole of two cells or more", () => {
    render(<Example timestamps={[T0, T0 + 60, T0 + 120, T0 + 180, T0 + 240, T0 + 300]} series={[{ label: "CPU", values: [1, null, 2, null, null, 3] }]} />);
    const plot = pagePlot();
    const gaps = plot.options.series[1]?.gaps?.(plot, 1, 0, 5) ?? [];
    expect(gaps).toHaveLength(1);
    const [from, to] = gaps[0] ?? [0, 0];
    expect(from).toBe(Math.round(plot.valToPos(T0 + 120, "x", true)));
    expect(to).toBe(Math.round(plot.valToPos(T0 + 300, "x", true)));
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Example />);
    await expectNoAxeViolations(container);
  });

  it("speaks Spanish once the language switches", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<Example resolution="medias de 1 min" />);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      `CPU, last 3 minutes, medias de 1 min. shop.example.com: último 30 %, media 30 %, pico 48 % (${formatChartTime(T0 + 60, false, "es")}).`.replaceAll(" %", "%"),
    );
    expect(screen.getByRole("button", { name: "Ampliar CPU" })).toBeInTheDocument();
  });

  it("has a skeleton exactly the shape of the chart, saying what loads", () => {
    render(<ChartSkeleton title="CPU" height={120} />);
    expect(screen.getByText("Loading the CPU chart")).toBeInTheDocument();
  });
});

describe("Chart readout", () => {
  it("shows the newest values when nothing is hovered, and no card", () => {
    render(<Example />);
    expect(readoutTime()).toHaveTextContent("Latest");
    expect(within(screen.getByRole("list", { name: "Series" })).getByText("30%")).toBeInTheDocument();
    expect(card()).toHaveAttribute("data-chart-card", "hidden");
  });

  it("follows the cursor in the row and in a card beside it", () => {
    render(<Example />);
    hover(pagePlot(), 1);
    expect(within(screen.getByRole("list", { name: "Series" })).getByText("48%")).toBeInTheDocument();
    const time = readoutTime().querySelector("time");
    expect(time).toHaveAttribute("dateTime", new Date((T0 + 60) * 1000).toISOString());
    const floating = card();
    expect(floating).toHaveAttribute("data-chart-card", "floating");
    expect(floating).toHaveAttribute("aria-hidden", "true");
    expect(floating).toHaveTextContent("1-minute average");
    expect(floating).toHaveTextContent("48%");
  });

  it("puts each cell's peak in the card beside its average", () => {
    render(<Example series={[{ label: "CPU", values: [12, 48, 30], peaks: [20, 92, 30] }]} />);
    hover(pagePlot(), 1);
    expect(card()).toHaveTextContent("Peak92%");
  });

  it("says in the card when a moment has no reading", () => {
    render(<Example series={[{ label: "CPU", values: [12, null, 30] }]} />);
    hover(pagePlot(), 1);
    expect(card()).toHaveTextContent("No reading: nothing was recorded at this moment.");
    expect(within(screen.getByRole("list", { name: "Series" })).getByText("–")).toBeInTheDocument();
  });

  it("lets Escape put the card away without moving the pointer or the focus (WCAG 1.4.13)", () => {
    render(<Example />);
    hover(pagePlot(), 1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(card()).toHaveAttribute("data-chart-card", "hidden");
    // The row, which does not depend on the card, still says the moment.
    expect(within(screen.getByRole("list", { name: "Series" })).getByText("48%")).toBeInTheDocument();
  });

  it("goes back to the newest values when the cursor leaves", () => {
    render(<Example />);
    hover(pagePlot(), 0);
    hover(pagePlot(), null);
    expect(readoutTime()).toHaveTextContent("Latest");
    expect(card()).toHaveAttribute("data-chart-card", "hidden");
  });

  it("places the card beside the cursor, and flips it left near the right edge", () => {
    const area = { left: 40, top: 8, width: 400, height: 144 };
    const size = { width: 160, height: 60 };
    const host = { width: 440, height: 160 };
    expect(placeCard({ left: 100, top: 20 }, area, size, host)).toEqual({ x: 152, y: 40 });
    const flipped = placeCard({ left: 380, top: 20 }, area, size, host);
    expect(flipped.x).toBe(40 + 380 - 12 - 160);
    const low = placeCard({ left: 100, top: 140 }, area, size, host);
    expect(low.y).toBe(8 + 140 - 12 - 60);
  });
});

describe("Chart keyboard", () => {
  it("is one tab stop, named by the title and described by its keys", () => {
    render(<Example />);
    const chart = screen.getByRole("application", { name: "CPU" });
    expect(chart).toHaveAttribute("tabindex", "0");
    expect(chart).toHaveAccessibleDescription(/Page Up and Page Down/);
  });

  it("steps cell by cell and ten at a time, jumps to the ends and clears with Escape", async () => {
    const user = userEvent.setup();
    const times = Array.from({ length: 30 }, (_, i) => T0 + i * 60);
    render(<Example timestamps={times} series={[{ label: "CPU", values: times.map((_, i) => i) }]} />);
    const series = () => within(screen.getByRole("list", { name: "Series" }));
    screen.getByRole("application", { name: "CPU" }).focus();
    await user.keyboard("{End}");
    expect(series().getByText("29%")).toBeInTheDocument();
    await user.keyboard("{PageUp}");
    expect(series().getByText("19%")).toBeInTheDocument();
    await user.keyboard("{ArrowLeft}");
    expect(series().getByText("18%")).toBeInTheDocument();
    await user.keyboard("{PageDown}");
    expect(series().getByText("28%")).toBeInTheDocument();
    await user.keyboard("{Home}");
    expect(series().getByText("0%")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(readoutTime()).toHaveTextContent("Latest");
  });

  it("never freezes: neither Enter nor a click pins a reading (owner item 62)", async () => {
    const user = userEvent.setup();
    render(<Example />);
    // uPlot's own lock is what a click toggled: off, a click is just a click.
    expect((plots[0]?.options as { cursor?: { lock?: boolean } }).cursor?.lock).toBe(false);
    screen.getByRole("application", { name: "CPU" }).focus();
    await user.keyboard("{Home}{Enter}");
    expect(card()).toHaveAttribute("data-chart-card", "floating");
    await user.keyboard("{Escape}");
    expect(card()).toHaveAttribute("data-chart-card", "hidden");
  });

  it("says each reading in a polite live region, with the peak and a gap in words", async () => {
    const user = userEvent.setup();
    render(<Example series={[{ label: "CPU", values: [12, null, 30], peaks: [31, null, 30] }]} />);
    screen.getByRole("application", { name: "CPU" }).focus();
    await user.keyboard("{Home}");
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    await waitFor(() => {
      expect(status).toHaveTextContent(`${formatChartTime(T0, false)}, CPU 12%, peak 31%`);
    });
    expect(readoutWords("14:01", [{ label: "CPU", values: [12, null], peaks: [31, null] }], 1, percent, bindT("en"))).toBe("14:01, CPU no reading");
  });
});

describe("Chart group", () => {
  function Pair() {
    return (
      <ChartGroup>
        <Chart title="CPU" timestamps={TIMES} series={[{ label: "CPU", values: [12, 48, 30] }]} formatValue={percent} />
        <Chart title="Memory" timestamps={TIMES} series={[{ label: "Used", values: [1, 2, 3] }]} formatValue={(v) => `${String(v)} GB`} />
      </ChartGroup>
    );
  }

  it("marks the same moment in every chart of the group; only the one under the pointer shows a card", () => {
    render(<Pair />);
    const [cpu, memory] = plots;
    if (cpu === undefined || memory === undefined) throw new Error("two plots");
    hover(cpu, 1);
    expect(memory.setCursor).toHaveBeenLastCalledWith({ left: memory.valToPos(T0 + 60, "x"), top: -10 }, false);
    const [cpuFigure, memoryFigure] = screen.getAllByRole("figure");
    if (cpuFigure === undefined || memoryFigure === undefined) throw new Error("two figures");
    // The other chart follows with its crosshair only: its row keeps the newest values until
    // the pointer is on it (owner item 62), so only one chart at a time says a moment.
    expect(readoutTime(memoryFigure)).toHaveTextContent("Latest");
    expect(within(memoryFigure).getByText("3 GB")).toBeInTheDocument();
    expect(card(cpuFigure)).toHaveAttribute("data-chart-card", "floating");
    expect(card(memoryFigure)).toHaveAttribute("data-chart-card", "hidden");
    hover(cpu, null);
    expect(memory.setCursor).toHaveBeenLastCalledWith({ left: -10, top: -10 }, false);
    expect(readoutTime(memoryFigure)).toHaveTextContent("Latest");
  });

  it("follows the keyboard too, not only the mouse", async () => {
    const user = userEvent.setup();
    render(<Pair />);
    const memory = plots[1];
    if (memory === undefined) throw new Error("two plots");
    screen.getByRole("application", { name: "CPU" }).focus();
    await user.keyboard("{Home}");
    expect(memory.setCursor).toHaveBeenLastCalledWith({ left: memory.valToPos(T0, "x"), top: -10 }, false);
  });

  it("gives every chart of the group the widest value axis", () => {
    render(<Pair />);
    const [cpu, memory] = plots;
    const size = (plot: FakePlot | undefined, labels: string[]) => plot?.options.axes[1]?.size?.(plot, labels) ?? 0;
    const narrow = size(cpu, ["0%", "50%"]);
    const wide = size(memory, ["1000.5 GB"]);
    expect(wide).toBeGreaterThan(narrow);
    expect(size(cpu, ["0%", "50%"])).toBe(wide);
  });
});

describe("Chart markers", () => {
  const inRange: ChartMarker = { at: T0 + 60, label: "Deploy 25, succeeded, 14:01", state: "running", href: "https://noust.example.com/deploys/25" };
  const outOfRange: ChartMarker = { at: T0 - 600, label: "Deploy 9, failed, 13:50", state: "failed", href: "https://noust.example.com/deploys/9" };

  it("draws an in-range marker as a focusable link with its full accessible name, and no other", () => {
    render(<Example markers={[inRange, outOfRange]} />);
    expect(screen.getByRole("link", { name: inRange.label })).toHaveAttribute("href", "https://noust.example.com/deploys/25");
    expect(screen.queryByRole("link", { name: outOfRange.label })).not.toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAccessibleName(/1 marker in view\.$/);
  });

  it("uses renderMarker to wrap the affordance, keeping Chart free of a router", () => {
    const marker: ChartMarker = {
      at: inRange.at,
      label: inRange.label,
      state: inRange.state,
      renderMarker: (m, children, linkProps) => (
        <button type="button" data-testid="custom-marker" aria-label={m.label} className={linkProps.className} style={linkProps.style}>
          {children}
        </button>
      ),
    };
    render(<Example markers={[marker]} />);
    expect(screen.getByTestId("custom-marker")).toHaveAccessibleName(marker.label);
  });

  it("falls back to an aria-disabled control rather than a link for an href that is not http(s)", () => {
    render(<Example markers={[{ ...inRange, href: "javascript:alert(1)" }]} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: inRange.label })).toHaveAttribute("aria-disabled", "true");
  });

  it("positions markers from uPlot's own geometry", () => {
    const plot: MarkerPlot = { valToPos: (v) => v - T0, bbox: { left: 80, top: 16, width: 800, height: 288 } };
    expect(positionMarkers(plot, [inRange], 2)).toEqual([{ marker: inRange, left: 40 + 60, top: 8 }]);
  });

  it("stacks markers closer than a target's width down their hairlines, and leaves apart ones on top", () => {
    const plot: MarkerPlot = { valToPos: (v) => v - T0, bbox: { left: 0, top: 0, width: 800, height: 288 } };
    const at = (offset: number) => ({ ...inRange, at: T0 + offset, label: `Deploy at ${String(offset)}` });
    const [a, b, c, d] = positionMarkers(plot, [at(100), at(110), at(115), at(200)], 1);
    expect([a?.top, b?.top, c?.top, d?.top]).toEqual([0, 26, 52, 0]);
    expect([a?.left, b?.left, c?.left, d?.left]).toEqual([100, 110, 115, 200]);
  });
});

describe("Chart expanded", () => {
  const TEN = Array.from({ length: 10 }, (_, i) => T0 + i * 60);
  const TEN_DOMAIN: ChartWindow = [T0, T0 + 540];

  function Expandable({ zoomed }: { zoomed?: boolean }) {
    const [zoom, setZoom] = useState<ChartWindow | null>(null);
    const fine: ChartData = { timestamps: [T0, T0 + 5, T0 + 10], series: [{ label: "shop.example.com", values: [40, 41, 42] }], domain: [T0, T0 + 10], step: 5, resolution: "a reading every 5 seconds" };
    return (
      <Chart
        title="CPU"
        description="Last 3 minutes"
        timestamps={TEN}
        series={[{ label: "shop.example.com", values: [12, 48, 30, 5, 6, 7, 8, 9, 10, 11] }]}
        formatValue={percent}
        domain={TEN_DOMAIN}
        step={60}
        resolution="1-minute averages"
        yRange={[0, 100]}
        rangeSelector={{ value: "1h", control: <button type="button">Range stand-in</button> }}
        {...(zoomed ? { zoom: { value: zoom, onChange: setZoom, data: zoom === null ? undefined : fine } } : {})}
      />
    );
  }

  async function expand(zoomed = false) {
    const user = userEvent.setup();
    render(<Expandable zoomed={zoomed} />);
    await user.click(screen.getByRole("button", { name: "Expand CPU" }));
    const dialog = await screen.findByRole("dialog", { name: "CPU" });
    const plot = plots.at(-1);
    if (plot === undefined || plot === plots[0]) throw new Error("the enlarged chart built no plot of its own");
    return { user, dialog, plot };
  }

  it("opens large with the page's range, the sentence of what it shows and the readout", async () => {
    const { dialog, plot } = await expand();
    expect(dialog).toHaveAccessibleDescription("Last 3 minutes");
    expect(within(dialog).getByRole("button", { name: "Range stand-in" })).toBeInTheDocument();
    expect(dialog.querySelector("[data-truth]")).toHaveTextContent(truthLine(bindT("en"), TEN_DOMAIN, "1-minute averages", null));
    hover(plot, 0);
    expect(within(within(dialog).getByRole("list", { name: "Series" })).getByText("12%")).toBeInTheDocument();
  });

  it("has the numbers as a table in a tab of the same height, newest first, with a summary", async () => {
    const { user, dialog } = await expand();
    await user.click(within(dialog).getByRole("tab", { name: "Data" }));
    const table = within(dialog).getByRole("table", { name: "CPU, newest first" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[1]?.textContent)).toEqual(["11%", "10%", "9%", "8%", "7%", "6%", "5%", "30%", "48%", "12%"]);
    expect(within(dialog).getByText("Peak").parentElement).toHaveTextContent("Peak 48%");
    expect(within(dialog).getByRole("region", { name: "CPU data" })).toHaveAttribute("tabindex", "0");
  });

  it("zooms by stretching the page's readings when the page reads nothing again, and resets", async () => {
    const { user, dialog, plot } = await expand();
    const reset = within(dialog).getByRole("button", { name: "Reset zoom" });
    expect(reset).toBeDisabled();
    act(() => {
      plot.select = { left: plot.valToPos(T0), top: 0, width: plot.valToPos(T0 + 180) - plot.valToPos(T0), height: 100 };
      plot.fire("setSelect");
    });
    await waitFor(() => {
      expect(plot.scales.x).toEqual({ min: T0, max: T0 + 180 });
    });
    expect(reset).toBeEnabled();
    await user.click(reset);
    expect(reset).toBeDisabled();
    expect(plot.scales.x).toEqual({ min: T0, max: T0 + 540 });
  });

  it("reads a zoomed stretch again through the page, and says the finer step", async () => {
    const { dialog, plot } = await expand(true);
    act(() => {
      plot.select = { left: plot.valToPos(T0), top: 0, width: plot.valToPos(T0 + 180) - plot.valToPos(T0), height: 100 };
      plot.fire("setSelect");
    });
    await waitFor(() => {
      expect(dialog.querySelector("[data-truth]")).toHaveTextContent("a reading every 5 seconds");
    });
    const latest = plots.at(-1);
    expect(latest?.data[1]).toEqual([40, 41, 42]);
  });

  it("zooms without dragging, for anyone who cannot drag (WCAG 2.5.7)", async () => {
    const { user, dialog } = await expand();
    await user.click(within(dialog).getByRole("button", { name: "Zoom in" }));
    expect(within(dialog).getByRole("button", { name: "Reset zoom" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Zoom out" }));
    expect(within(dialog).getByRole("button", { name: "Reset zoom" })).toBeDisabled();
  });

  it("has no accessibility violations, on the chart and on the data", async () => {
    const { user, dialog } = await expand();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("tab", { name: "Data" }));
    await expectNoAxeViolations(dialog);
  });
});

describe("Chart investigate", () => {
  const TEN = Array.from({ length: 10 }, (_, i) => T0 + i * 60);
  const TEN_DOMAIN: ChartWindow = [T0, T0 + 540];

  async function expand(investigate?: ChartProps["investigate"]) {
    const user = userEvent.setup();
    render(
      <Chart
        title="CPU"
        description="Last 10 minutes"
        timestamps={TEN}
        series={[{ label: "shop.example.com", values: [12, 48, 30, 5, 6, 7, 8, 9, 10, 11] }]}
        formatValue={percent}
        domain={TEN_DOMAIN}
        step={60}
        resolution="1-minute averages"
        {...(investigate ? { investigate } : {})}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Expand CPU" }));
    const dialog = await screen.findByRole("dialog", { name: "CPU" });
    const plot = plots.at(-1);
    if (plot === undefined) throw new Error("the enlarged chart built no plot");
    return { user, dialog, plot };
  }

  function Panel({ stretch, close }: { stretch: ChartWindow; close: () => void }) {
    return (
      <div role="group" aria-label="Investigation">
        <p>{`${String(stretch[0])}-${String(stretch[1])}`}</p>
        <button type="button" onClick={close}>
          Done
        </button>
      </div>
    );
  }

  const investigate = (stretch: ChartWindow, close: () => void) => <Panel stretch={stretch} close={close} />;

  it("is not offered where the page has nothing to open", async () => {
    const { dialog } = await expand();
    expect(within(dialog).queryByRole("button", { name: "Investigate this stretch" })).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("Drag across the chart to zoom into a stretch of time; it is read again at a finer step.");
  });

  it("opens what the page gives it for the whole window when nothing is selected, and closes it", async () => {
    const { user, dialog } = await expand(investigate);
    expect(dialog).toHaveTextContent("Drag across the chart to zoom into a stretch of time, then investigate what happened in it.");
    await user.click(within(dialog).getByRole("button", { name: "Investigate this stretch" }));
    const panel = screen.getByRole("group", { name: "Investigation" });
    expect(panel).toHaveTextContent(`${String(T0)}-${String(T0 + 540)}`);
    await user.click(within(panel).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("group", { name: "Investigation" })).not.toBeInTheDocument();
  });

  it("investigates the dragged selection once the chart has zoomed into it", async () => {
    const { user, dialog, plot } = await expand(investigate);
    act(() => {
      plot.select = { left: plot.valToPos(T0 + 60), top: 0, width: plot.valToPos(T0 + 240) - plot.valToPos(T0 + 60), height: 100 };
      plot.fire("setSelect");
    });
    await waitFor(() => {
      expect(plot.scales.x).toEqual({ min: T0 + 60, max: T0 + 240 });
    });
    await user.click(within(dialog).getByRole("button", { name: "Investigate this stretch" }));
    expect(screen.getByRole("group", { name: "Investigation" })).toHaveTextContent(`${String(T0 + 60)}-${String(T0 + 240)}`);
  });
});

describe("Chart helpers", () => {
  it("continues a description after the title", () => {
    expect(continuing("Last hour.")).toBe("last hour");
    expect(continuing("CPU of one core")).toBe("CPU of one core");
  });

  it("sizes the value axis to its widest label, never below the minimum", () => {
    const ctx = { font: "", measureText: (text: string) => ({ width: text.length * 7 }) } as unknown as CanvasRenderingContext2D;
    expect(valueAxisSize({ ctx }, ["1 KB/s", "771 KB/s"])).toBe(8 * 7 + 12);
    expect(valueAxisSize({ ctx }, [])).toBe(40);
  });
});
