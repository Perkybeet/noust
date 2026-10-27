import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import appCss from "../../styles/app.css?raw";
import tokensCss from "../../styles/tokens.css?raw";
import { ACTIVE_ITEM, SlidingIndicator, attributesOf, measureItem } from "./SlidingIndicator";

/** jsdom lays nothing out: give an element the room a browser would have given it. */
function room(element: HTMLElement, box: { x: number; y: number; width: number; height: number }, parent?: HTMLElement): void {
  Object.defineProperty(element, "offsetLeft", { configurable: true, value: box.x });
  Object.defineProperty(element, "offsetTop", { configurable: true, value: box.y });
  Object.defineProperty(element, "offsetWidth", { configurable: true, value: box.width });
  Object.defineProperty(element, "offsetHeight", { configurable: true, value: box.height });
  if (parent) Object.defineProperty(element, "offsetParent", { configurable: true, value: parent });
}

const ROW = [
  { x: 3, y: 3, width: 60, height: 30 },
  { x: 66, y: 3, width: 80, height: 30 },
  { x: 149, y: 3, width: 50, height: 30 },
];
const COLUMN = [
  { x: 0, y: 0, width: 200, height: 40 },
  { x: 0, y: 42, width: 200, height: 40 },
  { x: 0, y: 100, width: 200, height: 40 },
];

function Rail({ boxes, current }: { boxes: typeof ROW; current: number | null }) {
  return (
    <div data-testid="rail" className="relative isolate">
      <SlidingIndicator />
      {boxes.map((_, index) => (
        <button
          key={index}
          type="button"
          data-testid={`item-${String(index)}`}
          {...(current === index ? { "aria-pressed": true } : { "aria-pressed": false })}
        >
          Item {index}
        </button>
      ))}
    </div>
  );
}

/** The rail with each item laid out in `boxes`: the offsets are given before the effect measures. */
async function mount(boxes: typeof ROW, current: number | null) {
  const view = render(<Rail boxes={boxes} current={null} />);
  const rail = screen.getByTestId("rail");
  room(rail, { x: 0, y: 0, width: 300, height: 200 });
  boxes.forEach((box, index) => room(screen.getByTestId(`item-${String(index)}`), box, rail));
  // Re-render with the chosen item once the layout is known, as a router would after the first paint.
  await act(async () => {
    view.rerender(<Rail boxes={boxes} current={current} />);
    await Promise.resolve();
  });
  const indicator = rail.querySelector<HTMLElement>('[data-slot="sliding-indicator"]');
  if (!indicator) throw new Error("the indicator is not drawn");
  return { view, rail, indicator };
}

function vars(indicator: HTMLElement): { x: string; y: string; width: string; height: string } {
  return {
    x: indicator.style.getPropertyValue("--indicator-x"),
    y: indicator.style.getPropertyValue("--indicator-y"),
    width: indicator.style.getPropertyValue("--indicator-width"),
    height: indicator.style.getPropertyValue("--indicator-height"),
  };
}

async function choose(index: number): Promise<void> {
  await act(async () => {
    for (let other = 0; other < 3; other += 1) {
      const item = screen.queryByTestId(`item-${String(other)}`);
      item?.setAttribute("aria-pressed", other === index ? "true" : "false");
    }
    await Promise.resolve();
  });
}

const REAL_OBSERVER = window.ResizeObserver;

afterEach(() => {
  vi.unstubAllGlobals();
  window.ResizeObserver = REAL_OBSERVER;
});

describe("SlidingIndicator", () => {
  it("is where the chosen item is, in position and size, written as custom properties and never as a style attribute", async () => {
    const { indicator } = await mount(ROW, 1);
    expect(vars(indicator)).toEqual({ x: "66px", y: "3px", width: "80px", height: "30px" });
    expect(indicator.getAttribute("style") ?? "").not.toMatch(/(?:^|;)\s*(?:left|top|width|height|transform)\s*:/);
    expect(indicator).toHaveAttribute("data-state", "visible");
    expect(indicator).toHaveAttribute("aria-hidden", "true");
  });

  it("does not slide on the first frame: the item is already chosen when the page arrives", async () => {
    const { indicator } = await mount(ROW, 2);
    expect(vars(indicator).x).toBe("149px");
    expect(indicator).not.toHaveAttribute("data-animate");
  });

  it("slides to the item that is chosen next, with the new size, and is marked as moving", async () => {
    const { indicator } = await mount(ROW, 0);
    expect(indicator).not.toHaveAttribute("data-animate");
    await choose(2);
    expect(vars(indicator)).toEqual({ x: "149px", y: "3px", width: "50px", height: "30px" });
    expect(indicator).toHaveAttribute("data-animate");
  });

  it("follows a vertical list the same way, between groups separated by space", async () => {
    const { indicator } = await mount(COLUMN, 0);
    await choose(2);
    expect(vars(indicator)).toEqual({ x: "0px", y: "100px", width: "200px", height: "40px" });
    expect(indicator).toHaveAttribute("data-animate");
  });

  it("is instant with reduced motion: no marker for the transition to run on", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("prefers-reduced-motion"), media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const { indicator } = await mount(ROW, 0);
    await choose(1);
    expect(vars(indicator).x).toBe("66px");
    expect(indicator).not.toHaveAttribute("data-animate");
  });

  it("is hidden when no item is chosen (a screen with no entry), and appears in place, without sliding, when one is", async () => {
    const { indicator } = await mount(ROW, null);
    expect(indicator).toHaveAttribute("data-state", "hidden");
    await choose(1);
    expect(indicator).toHaveAttribute("data-state", "visible");
    expect(vars(indicator).x).toBe("66px");
    expect(indicator).not.toHaveAttribute("data-animate");
  });

  it("leaves a chosen item that disappears hidden, and does not slide from it to the next", async () => {
    const { indicator } = await mount(ROW, 0);
    await act(async () => {
      screen.getByTestId("item-0").setAttribute("aria-pressed", "false");
      await Promise.resolve();
    });
    expect(indicator).toHaveAttribute("data-state", "hidden");
    await choose(2);
    expect(indicator).not.toHaveAttribute("data-animate");
  });

  it("does not queue anything when the choice changes again before it arrives: the last one wins", async () => {
    const { indicator } = await mount(ROW, 0);
    await choose(1);
    await choose(2);
    await choose(0);
    await choose(2);
    expect(vars(indicator).x).toBe("149px");
    expect(indicator.getAttributeNames().filter((name) => name === "data-animate")).toHaveLength(1);
  });

  it("follows an item that changes size by itself at once, without sliding", async () => {
    let notify: () => void = () => undefined;
    class Observer {
      constructor(callback: () => void) {
        notify = callback;
      }
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    }
    window.ResizeObserver = Observer as unknown as typeof ResizeObserver;
    const { indicator } = await mount(ROW, 0);
    await choose(1);
    expect(indicator).toHaveAttribute("data-animate");
    room(screen.getByTestId("item-1"), { x: 66, y: 3, width: 36, height: 30 }, screen.getByTestId("rail"));
    act(() => {
      notify();
    });
    expect(vars(indicator).width).toBe("36px");
    expect(indicator).not.toHaveAttribute("data-animate");
  });

  it("stops watching when it leaves", async () => {
    const disconnect = vi.fn();
    class Observer {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = disconnect;
    }
    window.ResizeObserver = Observer;
    const { view } = await mount(ROW, 0);
    view.unmount();
    expect(disconnect).toHaveBeenCalled();
  });
});

describe("measureItem", () => {
  it("adds the offsets of every box between the item and the container (a link inside a list item)", () => {
    const container = document.createElement("nav");
    const li = document.createElement("li");
    const link = document.createElement("a");
    room(container, { x: 0, y: 0, width: 400, height: 40 });
    room(li, { x: 100, y: 2, width: 90, height: 36 }, container);
    room(link, { x: 4, y: 1, width: 80, height: 34 }, li);
    expect(measureItem(container, link)).toEqual({ x: 104, y: 3, width: 80, height: 34 });
  });
});

describe("what the indicator watches", () => {
  it("reads the attributes out of a selector", () => {
    expect(attributesOf('[data-active], [aria-current="page"], [aria-pressed="true"]')).toEqual(["data-active", "aria-current", "aria-pressed"]);
  });

  it("knows the attributes the kit and the router use for the chosen item", () => {
    for (const attribute of ["data-active", "aria-current", "aria-pressed", "aria-checked", "aria-selected", "data-checked", "data-status"]) {
      expect(ACTIVE_ITEM).toContain(attribute);
    }
  });
});

describe("how it moves, in the stylesheet", () => {
  const rule = /@utility sliding-indicator\s*{([\s\S]*?)\n}\n\n@utility|@utility sliding-indicator\s*{([\s\S]*?)\n}\n/.exec(appCss);
  const body = rule?.[1] ?? rule?.[2] ?? "";

  it("moves by translate and resizes by width and height, never by scale, which would stretch its corners", () => {
    expect(body).toMatch(/translate var\(--duration-move\) var\(--ease-standard\)/);
    expect(body).toMatch(/width var\(--duration-move\) var\(--ease-standard\)/);
    expect(body).toMatch(/height var\(--duration-move\) var\(--ease-standard\)/);
    expect(body).not.toMatch(/scale/);
  });

  it("transitions only when it is marked as moving", () => {
    const withoutMarker = body.replace(/&\[data-animate\][\s\S]*$/, "");
    expect(withoutMarker).not.toMatch(/transition/);
    expect(body).toMatch(/&\[data-animate\]\s*{\s*transition:/);
  });

  it("takes the move to zero with reduced motion, in the tokens and in the global rule", () => {
    expect(tokensCss).toMatch(/prefers-reduced-motion: reduce[\s\S]*--duration-move: 0ms;/);
    expect(appCss).toMatch(/prefers-reduced-motion: reduce\)\s*{[^@]*transition-duration: 0s !important/);
  });

  it("takes about 200ms by the standard curve", () => {
    expect(tokensCss).toMatch(/--duration-move: 200ms;/);
    expect(tokensCss).toMatch(/--ease-standard: cubic-bezier\(0\.4, 0, 0\.2, 1\);/);
  });
});
