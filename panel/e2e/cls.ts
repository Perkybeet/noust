/**
 * Cumulative Layout Shift, measured the web-vitals way: shared by the layout-shift gate and
 * the Spanish sweep, so a page is held to the same limit in every language.
 */

/** Above this a page fails: half of what the metric itself calls "good". */
export const CLS_LIMIT = 0.05;

/** Time after the page settles for anything late (a chart's second query, a font) to land. */
export const CLS_TAIL_MS = 1_500;

export interface ShiftRecord {
  value: number;
  time: number;
  sources: string[];
}

declare global {
  interface Window {
    __wasmShifts?: ShiftRecord[];
  }
}

/** Installed before the page's own scripts: records every shift not caused by input. */
export function observeLayoutShifts(): void {
  interface LayoutShift extends PerformanceEntry {
    value: number;
    hadRecentInput: boolean;
    sources: { node: Node | null; previousRect: DOMRectReadOnly; currentRect: DOMRectReadOnly }[];
  }
  const describe = (node: Node | null): string => {
    if (node === null) return "?";
    if (!(node instanceof Element)) return node.nodeName;
    const id = node.id ? `#${node.id}` : "";
    const cls = typeof node.className === "string" ? node.className.split(/\s+/).filter(Boolean).slice(0, 4).join(".") : "";
    const text = node.textContent.replace(/\s+/g, " ").trim().slice(0, 40);
    return `<${node.tagName.toLowerCase()}${id}${cls ? `.${cls}` : ""}> "${text}"`;
  };
  const shifts: ShiftRecord[] = [];
  window.__wasmShifts = shifts;
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries() as LayoutShift[]) {
      if (entry.hadRecentInput) continue;
      shifts.push({
        value: entry.value,
        time: entry.startTime,
        sources: entry.sources.map(
          (source) =>
            `${describe(source.node)} ${String(Math.round(source.previousRect.y))}->${String(Math.round(source.currentRect.y))}` +
            ` h${String(Math.round(source.previousRect.height))}->${String(Math.round(source.currentRect.height))}`,
        ),
      });
    }
  }).observe({ type: "layout-shift", buffered: true });
}

/** The web-vitals CLS: the largest window of shifts under 1 s apart and within 5 s overall. */
export function cumulativeLayoutShift(shifts: readonly ShiftRecord[]): number {
  let worst = 0;
  let current = 0;
  let first = 0;
  let previous = 0;
  for (const shift of shifts) {
    if (current > 0 && (shift.time - previous >= 1_000 || shift.time - first >= 5_000)) {
      current = 0;
    }
    if (current === 0) first = shift.time;
    current += shift.value;
    previous = shift.time;
    worst = Math.max(worst, current);
  }
  return worst;
}

/** The largest shifts and the elements that moved, for a failure message. */
export function describeShifts(shifts: readonly ShiftRecord[]): string {
  return [...shifts]
    .sort((a, b) => b.value - a.value)
    .slice(0, 6)
    .map((shift) => `  ${shift.value.toFixed(4)} at ${String(Math.round(shift.time))}ms\n    ${shift.sources.join("\n    ")}`)
    .join("\n");
}
