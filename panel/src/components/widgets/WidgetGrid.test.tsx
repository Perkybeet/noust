import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Package } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { rectOf } from "./dragMath";
import type { Geometry } from "./dragMath";
import { GAP_PX } from "./model";
import type { Placed, SizeKey } from "./model";
import type { FaceOf } from "./types";
import { useWidgetBoard } from "./useWidgetBoard";
import { DragGhost, WidgetGrid } from "./WidgetGrid";

const said = vi.hoisted(() => [] as string[]);
vi.mock("../../app/Announcer", () => ({
  announce: (message: string) => {
    said.push(message);
  },
}));

function item(id: string, size: SizeKey): Placed {
  return { id, type: id, size, settings: {} };
}

const TITLES: Record<string, string> = { alpha: "Alpha", beta: "Beta", gamma: "Gamma", delta: "Delta" };

/** Faces made of words: the grid knows nothing of what a widget is. `delta` takes one size only. */
const faceOf: FaceOf = (placed) => {
  const title = TITLES[placed.type];
  if (title === undefined) return null;
  const sizes: SizeKey[] = placed.type === "delta" ? ["small"] : ["small", "medium", "large", "tall", "wide", "xlarge"];
  return { title, icon: Package, sizes, link: <a href="/all">{`All ${title}`}</a>, body: (size) => <p>{`${title} body, ${size}`}</p> };
};

/** The window the grid is drawn in: 12 columns of 60px starting at (0, 0). */
const GEOMETRY: Geometry = { left: 0, top: 0, width: 12 * 60 + 11 * GAP_PX, columns: 12 };

/** What a test reads of the layout the page holds. */
class Probe {
  private current: readonly Placed[] = [];
  set = (layout: readonly Placed[]): void => {
    this.current = layout;
  };
  layout = (): readonly Placed[] => this.current;
}

function Harness({ start, editing = true, probe, extra }: { start: Placed[]; editing?: boolean; probe?: Probe; extra?: (board: ReturnType<typeof useWidgetBoard>) => ReactNode }) {
  const [layout, setLayout] = useState<readonly Placed[]>(start);
  useEffect(() => {
    probe?.set(layout);
  }, [probe, layout]);
  const board = useWidgetBoard({ layout, onLayoutChange: setLayout, faceOf, phone: false });
  return (
    <>
      <WidgetGrid board={board} layout={layout} onLayoutChange={setLayout} faceOf={faceOf} editing={editing} />
      {board.drag !== null ? <DragGhost drag={board.drag} faceOf={faceOf} /> : null}
      {extra?.(board)}
    </>
  );
}

function probe(): Probe {
  return new Probe();
}

function order(): string[] {
  return screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
}

/** jsdom lays nothing out: the grid is where GEOMETRY says, which is all the pointer needs. */
function layOut(): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const box = (left: number, top: number, width: number, height: number): DOMRect => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) });
    if (this.hasAttribute("data-widget-grid")) return box(GEOMETRY.left, GEOMETRY.top, GEOMETRY.width, 800);
    return box(0, 0, 0, 0);
  });
}

beforeEach(() => {
  said.length = 0;
});

describe("the grid", () => {
  it("places each widget where the layout says, on the grid of twelve", () => {
    render(<Harness start={[item("alpha", "wide"), item("beta", "medium"), item("gamma", "small")]} editing={false} />);
    const cells = [...document.querySelectorAll<HTMLElement>("[data-cell]")];
    expect(cells.map((cell) => [cell.dataset["cell"], cell.style.gridColumn, cell.style.gridRow])).toEqual([
      ["alpha", "1 / span 12", "1 / span 2"],
      ["beta", "1 / span 6", "3 / span 2"],
      ["gamma", "7 / span 3", "3 / span 2"],
    ]);
    expect(document.querySelector<HTMLElement>("[data-widget-grid]")?.style.gridTemplateColumns).toBe("repeat(12, minmax(0, 1fr))");
  });

  it("gives each widget its title as a heading, its link and its body for the size it has", () => {
    render(<Harness start={[item("alpha", "wide"), item("beta", "small")]} editing={false} />);
    expect(order()).toEqual(["Alpha", "Beta"]);
    expect(screen.getByRole("link", { name: "All Alpha" })).toBeInTheDocument();
    expect(screen.getByText("Alpha body, wide")).toBeInTheDocument();
    expect(screen.getByText("Beta body, small")).toBeInTheDocument();
  });

  it("leaves out a widget this console has no face for", () => {
    render(<Harness start={[item("alpha", "small"), item("nowhere", "small")]} editing={false} />);
    expect(order()).toEqual(["Alpha"]);
  });

  it("draws no controls until the panel is arranged", () => {
    render(<Harness start={[item("alpha", "small")]} editing={false} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("draws, when it is arranged, a handle, a size and a remove button on each widget, named by the widget", () => {
    render(<Harness start={[item("alpha", "small"), item("beta", "medium")]} />);
    for (const title of ["Alpha", "Beta"]) {
      expect(screen.getByRole("button", { name: `Move ${title}` })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: new RegExp(`^Size and position of ${title}: `) })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: `Remove ${title}` })).toBeInTheDocument();
    }
    // The link goes while a panel is arranged: a press on a widget is not a way to leave.
    expect(screen.queryByRole("link", { name: "All Alpha" })).not.toBeInTheDocument();
  });

  it("has no accessibility violation, arranged or not", async () => {
    const { container, rerender } = render(<Harness start={[item("alpha", "small"), item("beta", "medium")]} editing={false} />);
    await expectNoAxeViolations(container);
    rerender(<Harness start={[item("alpha", "small"), item("beta", "medium")]} />);
    await expectNoAxeViolations(container);
  });
});

describe("arranging with the keyboard", () => {
  it("lifts a widget with Space, moves it with the arrow keys and drops it with Space, saying each step", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small"), item("gamma", "small")]} probe={state} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    handle.focus();
    await user.keyboard(" ");
    expect(handle).toHaveAttribute("aria-pressed", "true");
    expect(said.at(-1)).toBe("Alpha lifted. The arrow keys move it, Space drops it and Escape puts it back.");
    await user.keyboard("{ArrowRight}");
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha", "gamma"]);
    expect(said.at(-1)).toBe("Alpha: position 2 of 3.");
    await user.keyboard("{ArrowDown}");
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "gamma", "alpha"]);
    // The widget moved in the document and the keyboard went with it.
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Alpha" })).toHaveFocus());
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Move Alpha" })).toHaveAttribute("aria-pressed", "false");
    expect(said.at(-1)).toBe("Alpha placed at position 3.");
    expect(order()).toEqual(["Beta", "Gamma", "Alpha"]);
  });

  it("does not move a widget that is not lifted, and stops at the ends", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} probe={state} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    handle.focus();
    await user.keyboard("{ArrowRight}");
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
    await user.keyboard(" {ArrowLeft}");
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha"]);
  });

  it("puts a lifted widget back where it was with Escape", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small"), item("gamma", "small")]} probe={state} />);
    screen.getByRole("button", { name: "Move Alpha" }).focus();
    await user.keyboard(" {ArrowRight}{ArrowRight}");
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "gamma", "alpha"]);
    await user.keyboard("{Escape}");
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta", "gamma"]);
    expect(said.at(-1)).toBe("Alpha put back where it was.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Alpha" })).toHaveFocus());
  });

  it("drops a lifted widget when the keyboard leaves it", async () => {
    const user = userEvent.setup();
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    handle.focus();
    await user.keyboard(" ");
    expect(handle).toHaveAttribute("aria-pressed", "true");
    await user.tab();
    expect(handle).toHaveAttribute("aria-pressed", "false");
  });

  it("describes the keys to the handle, so a screen reader hears how to use it", () => {
    render(<Harness start={[item("alpha", "small")]} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    const help = handle.getAttribute("aria-describedby");
    expect(help).not.toBeNull();
    expect(document.getElementById(help ?? "")).toHaveTextContent("Press Space to lift the widget");
  });
});

describe("arranging from the menu of a widget's size", () => {
  it("offers only the sizes the widget takes, with the one it has marked", async () => {
    const user = userEvent.setup();
    render(<Harness start={[item("delta", "small"), item("alpha", "medium")]} />);
    await user.click(screen.getByRole("button", { name: "Size and position of Delta: Small" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual(["Smallcurrent size", "Move earlier", "Move later"]);
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Size and position of Alpha: Medium" }));
    const sizes = await screen.findByRole("menu");
    expect(within(sizes).getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual([
      "Small",
      "Mediumcurrent size",
      "Large",
      "Tall",
      "Wide",
      "Extra large",
      "Move earlier",
      "Move later",
    ]);
  });

  it("changes the size, and says it", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "medium")]} probe={state} />);
    await user.click(screen.getByRole("button", { name: "Size and position of Alpha: Medium" }));
    await user.click(await screen.findByRole("menuitem", { name: "Wide" }));
    expect(state.layout()[0]?.size).toBe("wide");
    expect(said.at(-1)).toBe("Alpha: size wide.");
    expect(screen.getByText("Alpha body, wide")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Size and position of Alpha: Wide" })).toBeInTheDocument();
  });

  it("moves a widget earlier or later, and cannot move the first one earlier or the last one later", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} probe={state} />);
    await user.click(screen.getByRole("button", { name: "Size and position of Alpha: Small" }));
    expect(await screen.findByRole("menuitem", { name: "Move earlier" })).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByRole("menuitem", { name: "Move later" }));
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha"]);
    expect(said.at(-1)).toBe("Alpha: position 2 of 2.");
    await user.click(screen.getByRole("button", { name: "Size and position of Alpha: Small" }));
    expect(await screen.findByRole("menuitem", { name: "Move later" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("removing a widget", () => {
  it("lifts it away, takes it out, says so and gives the keyboard to its neighbour", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small"), item("gamma", "small")]} probe={state} />);
    await user.click(screen.getByRole("button", { name: "Remove Beta" }));
    await waitFor(() => expect(state.layout().map((p) => p.id)).toEqual(["alpha", "gamma"]));
    expect(said.at(-1)).toBe("Beta removed from the panel.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Gamma" })).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "Remove Gamma" }));
    await waitFor(() => expect(state.layout().map((p) => p.id)).toEqual(["alpha"]));
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Alpha" })).toHaveFocus());
  });
});

describe("arranging with a pointer", () => {
  beforeEach(() => {
    layOut();
    // jsdom does not scroll; a drag near the edge of the window asks it to.
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
  });

  const slotOf = (slot: { x: number; y: number; w: number; h: number }) => rectOf(slot, GEOMETRY);

  it("moves a widget to where it is dropped, and puts a copy of it in the pointer's hand while it is held", async () => {
    const state = probe();
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium"), item("gamma", "wide")]} probe={state} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    const second = slotOf({ x: 6, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(handle, { pointerType: "mouse", button: 0, clientX: first.left + 10, clientY: first.top + 10 });
    // The widget is still in the panel as a hollow, and the copy is what moves.
    expect(document.querySelectorAll("[aria-hidden=true] h2")).toHaveLength(1);
    fireEvent.pointerMove(window, { clientX: second.left + second.width - 6, clientY: second.top + 10 });
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha", "gamma"]);
    fireEvent.pointerUp(window);
    await waitFor(() => expect(document.querySelectorAll("[aria-hidden=true] h2")).toHaveLength(0));
    expect(said.at(-1)).toBe("Alpha placed at position 2.");
    expect(order()).toEqual(["Beta", "Alpha", "Gamma"]);
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
  });

  it("starts a drag from the widget's body with a mouse, but not from a button on it and not with a finger", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium")]} probe={state} />);
    const cell = document.querySelector<HTMLElement>("[data-cell=alpha]");
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    const second = slotOf({ x: 6, y: 0, w: 6, h: 2 });
    if (cell === null) throw new Error("no cell");
    fireEvent.pointerDown(within(cell).getByRole("button", { name: "Remove Alpha" }), { pointerType: "mouse", button: 0, clientX: first.left, clientY: first.top });
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
    fireEvent.pointerDown(cell, { pointerType: "touch", button: 0, clientX: first.left, clientY: first.top });
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
    fireEvent.pointerDown(cell, { pointerType: "mouse", button: 0, clientX: first.left + 20, clientY: first.top + 50 });
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(true);
    fireEvent.pointerMove(window, { clientX: second.left + second.width - 6, clientY: second.top + 10 });
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha"]);
    fireEvent.pointerUp(window);
  });

  it("puts everything back when Escape is pressed with a widget in the hand", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium")]} probe={state} />);
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    const second = slotOf({ x: 6, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: first.left + 10, clientY: first.top + 10 });
    fireEvent.pointerMove(window, { clientX: second.left + second.width - 6, clientY: second.top + 10 });
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha"]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
    expect(said.at(-1)).toBe("Alpha put back where it was.");
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
  });

  it("brings in a widget dragged from the gallery only while the pointer is over the panel", async () => {
    const state = probe();
    render(
      <Harness
        start={[item("alpha", "medium")]}
        probe={state}
        extra={(board) => (
          <button type="button" onPointerDown={(event) => board.startAdd(item("beta", "medium"), event)}>
            Gallery beta
          </button>
        )}
      />,
    );
    const gallery = screen.getByRole("button", { name: "Gallery beta" });
    const second = slotOf({ x: 6, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(gallery, { pointerType: "mouse", button: 0, clientX: 2000, clientY: 400 });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha"]);
    fireEvent.pointerMove(window, { clientX: second.left + 20, clientY: second.top + 10 });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
    fireEvent.pointerMove(window, { clientX: 2000, clientY: 400 });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha"]);
    fireEvent.pointerMove(window, { clientX: second.left + 20, clientY: second.top + 10 });
    fireEvent.pointerUp(window);
    await waitFor(() => expect(document.querySelectorAll("[aria-hidden=true] h2")).toHaveLength(0));
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
    expect(order()).toEqual(["Alpha", "Beta"]);
  });

  it("brings in nothing when a widget from the gallery is dropped outside the panel", () => {
    const state = probe();
    render(
      <Harness
        start={[item("alpha", "medium")]}
        probe={state}
        extra={(board) => (
          <button type="button" onPointerDown={(event) => board.startAdd(item("beta", "medium"), event)}>
            Gallery beta
          </button>
        )}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("button", { name: "Gallery beta" }), { pointerType: "mouse", button: 0, clientX: 2000, clientY: 400 });
    fireEvent.pointerMove(window, { clientX: 2100, clientY: 410 });
    fireEvent.pointerUp(window);
    expect(state.layout().map((p) => p.id)).toEqual(["alpha"]);
  });

  it("resizes a widget by its corner to the nearest size it takes", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("delta", "small")]} probe={state} />);
    const corner = document.querySelector<HTMLElement>("[data-cell=alpha] [data-resize]");
    if (corner === null) throw new Error("no corner");
    fireEvent.pointerDown(corner, { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    // Three more columns and no more rows: a medium widget.
    fireEvent.pointerMove(window, { clientX: 100 + 3 * (60 + GAP_PX), clientY: 100 });
    expect(state.layout()[0]?.size).toBe("medium");
    // Two rows more as well: a large one.
    fireEvent.pointerMove(window, { clientX: 100 + 3 * (60 + GAP_PX), clientY: 100 + 2 * (80 + GAP_PX) });
    expect(state.layout()[0]?.size).toBe("large");
    fireEvent.pointerUp(window);
    expect(said.at(-1)).toBe("Alpha: size large.");
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
  });

  it("gives no corner to a widget that takes one size only", () => {
    render(<Harness start={[item("delta", "small")]} />);
    expect(document.querySelector("[data-resize]")).toBeNull();
  });
});
