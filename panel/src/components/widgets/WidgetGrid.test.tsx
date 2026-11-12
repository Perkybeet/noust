import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Package } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

/** The offers to undo the board makes: the words of the toast and what its button does. */
const offered = vi.hoisted(() => [] as { title: string; undo: (() => void) | undefined; timeout: number | undefined }[]);
vi.mock("../ui/toast", () => {
  const info = (title: string, options?: { action?: { onClick: () => void }; timeout?: number }): string => {
    offered.push({ title, undo: options?.action?.onClick, timeout: options?.timeout });
    return "undo";
  };
  return { toast: { info, success: info, warning: info, error: info, dismiss: vi.fn() } };
});

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

function Harness({ start, editing = true, probe, extra, phone = false }: { start: Placed[]; editing?: boolean; probe?: Probe; extra?: (board: ReturnType<typeof useWidgetBoard>) => ReactNode; phone?: boolean }) {
  const [layout, setLayout] = useState<readonly Placed[]>(start);
  useEffect(() => {
    probe?.set(layout);
  }, [probe, layout]);
  const board = useWidgetBoard({ layout, onLayoutChange: setLayout, faceOf, phone });
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
  offered.length = 0;
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

  it("resizes a widget by its corner to the nearest size it takes: an outline says which, and the widget changes when the corner is let go", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("delta", "small")]} probe={state} />);
    const corner = document.querySelector<HTMLElement>("[data-cell=alpha] [data-resize]");
    if (corner === null) throw new Error("no corner");
    fireEvent.pointerDown(corner, { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    // Three more columns and no more rows: a medium widget, in the outline. Nothing around it has moved to make room yet.
    fireEvent.pointerMove(window, { clientX: 100 + 3 * (60 + GAP_PX), clientY: 100 });
    expect(document.querySelector("[data-resize-outline]")).toHaveAttribute("data-resize-outline", "medium");
    expect(state.layout()[0]?.size).toBe("small");
    // Two rows more as well: a large one.
    fireEvent.pointerMove(window, { clientX: 100 + 3 * (60 + GAP_PX), clientY: 100 + 2 * (80 + GAP_PX) });
    expect(document.querySelector("[data-resize-outline]")).toHaveAttribute("data-resize-outline", "large");
    expect(state.layout()[0]?.size).toBe("small");
    fireEvent.pointerUp(window);
    expect(state.layout()[0]?.size).toBe("large");
    expect(document.querySelector("[data-resize-outline]")).toBeNull();
    expect(said.at(-1)).toBe("Alpha: size large.");
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
  });

  it("gives no corner to a widget that takes one size only", () => {
    render(<Harness start={[item("delta", "small")]} />);
    expect(document.querySelector("[data-resize]")).toBeNull();
  });
});

describe("what a held widget shows", () => {
  beforeEach(() => {
    layOut();
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
  });

  const slotOf = (slot: { x: number; y: number; w: number; h: number }) => rectOf(slot, GEOMETRY);

  it("leaves where it will land as an outline of its real size, with the name of that size", () => {
    render(<Harness start={[item("alpha", "medium"), item("beta", "large")]} />);
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: first.left + 10, clientY: first.top + 10 });
    const place = document.querySelector("[data-cell=alpha] [data-drop-place]");
    expect(place).toHaveAttribute("data-drop-place", "medium");
    expect(place).toHaveTextContent("Medium, 2 × 1");
    // It is a place and not a copy of the widget, which stays in the document unseen under it: a finger's touch events go to the element it pressed.
    expect(place?.textContent).not.toContain("Alpha");
    expect(document.querySelector("[data-cell=alpha] h2")?.closest(".invisible")).not.toBeNull();
    fireEvent.pointerUp(window);
  });

  it("moves the copy with the pointer without drawing the panel again", () => {
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium")]} />);
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: first.left + 30, clientY: first.top + 12 });
    const ghost = document.querySelector<HTMLElement>("div.fixed[aria-hidden=true]");
    if (ghost === null) throw new Error("no copy in the hand");
    fireEvent.pointerMove(window, { clientX: 300, clientY: 200 });
    // Where the pointer took hold of the widget is kept: 30 and 12 from its corner.
    expect(ghost.style.transform).toContain("translate(270px, 188px)");
    fireEvent.pointerMove(window, { clientX: 310, clientY: 220 });
    expect(ghost.style.transform).toContain("translate(280px, 208px)");
    fireEvent.pointerUp(window);
  });

  it("brings a widget from the gallery to the place it is dropped on, not to the end, as a card in the hand and an outline in the panel", async () => {
    const state = probe();
    render(
      <Harness
        start={[item("alpha", "medium"), item("gamma", "medium")]}
        probe={state}
        extra={(board) => (
          <button type="button" onPointerDown={(event) => board.startAdd(item("beta", "large"), event)}>
            Gallery beta
          </button>
        )}
      />,
    );
    const second = slotOf({ x: 6, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Gallery beta" }), { pointerType: "mouse", button: 0, clientX: 2000, clientY: 400 });
    // Over the left half of the second widget: before it, which is the middle of the panel.
    fireEvent.pointerMove(window, { clientX: second.left + 20, clientY: second.top + 10 });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta", "gamma"]);
    expect(document.querySelector("[data-cell=beta] [data-drop-place]")).toHaveTextContent("Large, 2 × 2");
    // The card in the hand is the gallery's: the title and no widget behind it.
    const ghost = document.querySelector("div.fixed[aria-hidden=true]");
    expect(ghost).toHaveTextContent("Beta");
    expect(ghost?.querySelector("h2")).toBeNull();
    fireEvent.pointerUp(window);
    await waitFor(() => expect(document.querySelector("div.fixed[aria-hidden=true]")).toBeNull());
    expect(order()).toEqual(["Alpha", "Beta", "Gamma"]);
    expect(said.at(-1)).toBe("Beta placed at position 2.");
  });
});

describe("changing a size by the edges and the corner", () => {
  beforeEach(() => {
    layOut();
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
  });

  const column = 60 + GAP_PX;
  const band = 80 + GAP_PX;
  const grip = (id: string, which: "right" | "bottom" | "corner"): HTMLElement => {
    const found = document.querySelector<HTMLElement>(which === "corner" ? `[data-cell=${id}] [data-resize=corner]` : `[data-cell=${id}] [data-resize-edge=${which}]`);
    if (found === null) throw new Error(`no ${which} grip on ${id}`);
    return found;
  };

  it("gives an edge only where the widget takes another size along it, and a corner where it takes any", () => {
    render(<Harness start={[item("alpha", "small"), item("delta", "small")]} />);
    expect(["right", "bottom", "corner"].map((which) => document.querySelectorAll(`[data-cell=alpha] ${which === "corner" ? "[data-resize=corner]" : `[data-resize-edge=${which}]`}`).length)).toEqual([1, 1, 1]);
    expect(document.querySelectorAll("[data-cell=delta] [data-resize], [data-cell=delta] [data-resize-edge]")).toHaveLength(0);
  });

  it("gives a phone's widgets the corner only: a finger down an edge would be scrolling the page", () => {
    render(<Harness start={[item("alpha", "small")]} phone />);
    expect(document.querySelectorAll("[data-cell=alpha] [data-resize=corner]")).toHaveLength(1);
    expect(document.querySelectorAll("[data-cell=alpha] [data-resize-edge]")).toHaveLength(0);
  });

  it("draws no grips until the panel is arranged, and none on the widget that is held", () => {
    const { rerender } = render(<Harness start={[item("alpha", "small")]} editing={false} />);
    expect(document.querySelectorAll("[data-resize], [data-resize-edge]")).toHaveLength(0);
    rerender(<Harness start={[item("alpha", "small")]} />);
    expect(document.querySelectorAll("[data-resize], [data-resize-edge]")).toHaveLength(3);
  });

  it("takes the right edge to a width and nothing else, whatever the pointer does downward", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "small")]} probe={state} />);
    fireEvent.pointerDown(grip("alpha", "right"), { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 100 + 3 * column, clientY: 100 + 2 * band });
    expect(document.querySelector("[data-resize-outline]")).toHaveAttribute("data-resize-outline", "medium");
    fireEvent.pointerUp(window);
    expect(state.layout()[0]?.size).toBe("medium");
  });

  it("takes the bottom edge to a height and nothing else, whatever the pointer does sideways", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "small")]} probe={state} />);
    fireEvent.pointerDown(grip("alpha", "bottom"), { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 100 + 9 * column, clientY: 100 + 2 * band });
    expect(document.querySelector("[data-resize-outline]")).toHaveAttribute("data-resize-outline", "tall");
    fireEvent.pointerUp(window);
    expect(state.layout()[0]?.size).toBe("tall");
  });

  it("draws the outline where the widget will be, at the room its size takes, and says the size", () => {
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} />);
    fireEvent.pointerDown(grip("beta", "right"), { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 100 + 3 * column, clientY: 100 });
    const outline = document.querySelector<HTMLElement>("[data-resize-outline]");
    // Beta is the second of the row, at the fourth column; as a medium one it takes six of them from there.
    expect(outline?.style.left).toBe(`${String(3 * column)}px`);
    expect(outline?.style.top).toBe("0px");
    expect(outline?.style.width).toBe(`${String(6 * 60 + 5 * GAP_PX)}px`);
    expect(outline?.style.height).toBe(`${String(2 * 80 + GAP_PX)}px`);
    expect(outline).toHaveTextContent("Medium, 2 × 1");
    // The widgets around it stay as they are until it is let go.
    expect(document.querySelector<HTMLElement>("[data-cell=beta]")?.style.gridColumn).toBe("4 / span 3");
    fireEvent.pointerUp(window);
  });

  it("puts the widget back as it was with Escape, and leaves nothing behind", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "small")]} probe={state} />);
    fireEvent.pointerDown(grip("alpha", "corner"), { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 100 + 9 * column, clientY: 100 + 2 * band });
    expect(document.querySelector("[data-resize-outline]")).toHaveAttribute("data-resize-outline", "xlarge");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.querySelector("[data-resize-outline]")).toBeNull();
    expect(state.layout()[0]?.size).toBe("small");
    expect(document.documentElement.classList.contains("widget-dragging")).toBe(false);
    expect(offered).toHaveLength(0);
  });

  it("does not count the page scrolling under a still hand as a hand that did not move", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "small")]} probe={state} />);
    fireEvent.pointerDown(grip("alpha", "bottom"), { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    // The page has gone down two bands while the pointer stayed: the widget is two bands taller.
    Object.defineProperty(window, "scrollY", { configurable: true, value: 2 * band });
    fireEvent.pointerMove(window, { clientX: 100, clientY: 100 });
    expect(document.querySelector("[data-resize-outline]")).toHaveAttribute("data-resize-outline", "tall");
    fireEvent.pointerUp(window);
    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
  });
});

describe("the page under a held widget", () => {
  /** The frames of the window, run by hand: a test says how many and how far apart, and waits for none of them. */
  function frames() {
    const queue = new Map<number, FrameRequestCallback>();
    let next = 1;
    let clock = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      next += 1;
      queue.set(next, callback);
      return next;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      queue.delete(id);
    });
    return {
      run(count: number, step = 16): void {
        for (let n = 0; n < count; n += 1) {
          clock += step;
          const due = [...queue.values()];
          queue.clear();
          act(() => {
            for (const callback of due) callback(clock);
          });
        }
      },
    };
  }

  beforeEach(() => layOut());

  it("scrolls while the pointer rests at the bottom edge, with no more movement, and by more the nearer the edge", () => {
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const clock = frames();
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium")]} />);
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: 100, clientY: window.innerHeight - 10 });
    clock.run(30);
    const nearEdge = scroll.mock.calls.reduce((sum, [, by]) => sum + by, 0);
    expect(scroll.mock.calls.length).toBeGreaterThan(10);
    for (const [left, by] of scroll.mock.calls) {
      expect(left).toBe(0);
      expect(by).toBeGreaterThan(0);
    }
    // Thirty frames of sixteen milliseconds near the edge: most of the top speed (840 px a second) for half a second.
    expect(nearEdge).toBeGreaterThan(300);
    expect(nearEdge).toBeLessThan(420);
    fireEvent.pointerUp(window);
  });

  it("scrolls up at the top, and not at all in the middle of the window", () => {
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const clock = frames();
    render(<Harness start={[item("alpha", "medium")]} />);
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: 100, clientY: window.innerHeight / 2 });
    clock.run(20);
    expect(scroll).not.toHaveBeenCalled();
    fireEvent.pointerMove(window, { clientX: 100, clientY: 20 });
    clock.run(10);
    expect(scroll.mock.calls.length).toBeGreaterThan(0);
    for (const [, by] of scroll.mock.calls) expect(by).toBeLessThan(0);
    fireEvent.pointerUp(window);
  });

  it("stops when the widget is let go, and when Escape puts it back", () => {
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const clock = frames();
    render(<Harness start={[item("alpha", "medium")]} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    fireEvent.pointerDown(handle, { pointerType: "mouse", button: 0, clientX: 100, clientY: window.innerHeight - 5 });
    clock.run(5);
    fireEvent.pointerUp(window);
    const after = scroll.mock.calls.length;
    clock.run(20);
    expect(scroll.mock.calls.length).toBe(after);
    fireEvent.pointerDown(handle, { pointerType: "mouse", button: 0, clientX: 100, clientY: window.innerHeight - 5 });
    clock.run(5);
    fireEvent.keyDown(window, { key: "Escape" });
    const escaped = scroll.mock.calls.length;
    clock.run(20);
    expect(scroll.mock.calls.length).toBe(escaped);
  });

  it("asks again where the widget goes when the page has moved under it", () => {
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const state = probe();
    const clock = frames();
    render(<Harness start={[item("alpha", "wide"), item("beta", "wide")]} probe={state} />);
    const second = rectOf({ x: 0, y: 2, w: 12, h: 2 }, GEOMETRY);
    // Held by the bottom edge of the window, with the second widget under it: the pointer says "after it".
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: 100, clientY: window.innerHeight - 10 });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
    // The page scrolls up the grid by a band and a half: the pointer, which has not moved, is now over the second widget's lower half.
    const grid = document.querySelector<HTMLElement>("[data-widget-grid]");
    if (grid === null) throw new Error("no grid");
    const box = (top: number): DOMRect => ({ left: 0, top, width: GEOMETRY.width, height: 800, right: GEOMETRY.width, bottom: top + 800, x: 0, y: top, toJSON: () => ({}) });
    vi.spyOn(grid, "getBoundingClientRect").mockImplementation(() => box(window.innerHeight - 10 - second.top - second.height + 5));
    clock.run(4);
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha"]);
    fireEvent.pointerUp(window);
  });
});

describe("a finger", () => {
  beforeEach(() => {
    layOut();
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  const lifted = (): boolean => document.documentElement.classList.contains("widget-dragging");
  const cellOf = (id: string): HTMLElement => {
    const cell = document.querySelector<HTMLElement>(`[data-cell=${id}]`);
    if (cell === null) throw new Error("no cell");
    return cell;
  };

  it("lifts a widget by holding it, and not before", () => {
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium")]} />);
    fireEvent.pointerDown(cellOf("alpha"), { pointerType: "touch", pointerId: 7, button: 0, clientX: 40, clientY: 40 });
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(lifted()).toBe(false);
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(lifted()).toBe(true);
    expect(document.querySelector("[data-cell=alpha] [data-drop-place]")).not.toBeNull();
    fireEvent.pointerUp(window, { pointerId: 7 });
    expect(lifted()).toBe(false);
  });

  it("does not lift a widget it is only scrolling past, or one it lets go of at once", () => {
    render(<Harness start={[item("alpha", "medium")]} />);
    fireEvent.pointerDown(cellOf("alpha"), { pointerType: "touch", pointerId: 7, button: 0, clientX: 40, clientY: 40 });
    fireEvent.pointerMove(window, { pointerId: 7, clientX: 40, clientY: 80 });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(lifted()).toBe(false);
    fireEvent.pointerDown(cellOf("alpha"), { pointerType: "touch", pointerId: 8, button: 0, clientX: 40, clientY: 40 });
    fireEvent.pointerUp(window, { pointerId: 8 });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(lifted()).toBe(false);
  });

  it("forgives a hand that is not perfectly still while it holds", () => {
    render(<Harness start={[item("alpha", "medium")]} />);
    fireEvent.pointerDown(cellOf("alpha"), { pointerType: "touch", pointerId: 7, button: 0, clientX: 40, clientY: 40 });
    fireEvent.pointerMove(window, { pointerId: 7, clientX: 44, clientY: 43 });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(lifted()).toBe(true);
    fireEvent.pointerUp(window, { pointerId: 7 });
  });

  it("takes the handle at once: it is a grip, and does not scroll", () => {
    render(<Harness start={[item("alpha", "medium")]} />);
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "touch", pointerId: 7, button: 0, clientX: 40, clientY: 40 });
    expect(lifted()).toBe(true);
    fireEvent.pointerUp(window, { pointerId: 7 });
  });

  it("does not scroll the page while it holds a widget, and does when it holds none", () => {
    render(<Harness start={[item("alpha", "medium")]} />);
    const move = (): Event => {
      const event = new Event("touchmove", { cancelable: true, bubbles: true });
      window.dispatchEvent(event);
      return event;
    };
    expect(move().defaultPrevented).toBe(false);
    fireEvent.pointerDown(cellOf("alpha"), { pointerType: "touch", pointerId: 7, button: 0, clientX: 40, clientY: 40 });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(move().defaultPrevented).toBe(true);
    fireEvent.pointerUp(window, { pointerId: 7 });
    expect(move().defaultPrevented).toBe(false);
  });

  it("brings in a widget of the gallery by holding its card", () => {
    render(
      <Harness
        start={[item("alpha", "medium")]}
        extra={(board) => (
          <button type="button" onPointerDown={(event) => board.startAdd(item("beta", "medium"), event)}>
            Gallery beta
          </button>
        )}
      />,
    );
    fireEvent.pointerDown(screen.getByRole("button", { name: "Gallery beta" }), { pointerType: "touch", pointerId: 7, button: 0, clientX: 2000, clientY: 400 });
    expect(lifted()).toBe(false);
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(lifted()).toBe(true);
    fireEvent.pointerUp(window, { pointerId: 7 });
  });

  it("is told what its press and hold made when the widget is a long press away from the menu a browser would open", () => {
    render(<Harness start={[item("alpha", "medium")]} />);
    const menu = new Event("contextmenu", { cancelable: true, bubbles: true });
    cellOf("alpha").dispatchEvent(menu);
    expect(menu.defaultPrevented).toBe(true);
  });
});

describe("undoing", () => {
  beforeEach(() => {
    layOut();
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
  });

  const slotOf = (slot: { x: number; y: number; w: number; h: number }) => rectOf(slot, GEOMETRY);

  it("offers to put a widget back where it was after it is dragged somewhere else, and does", () => {
    const state = probe();
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium"), item("gamma", "wide")]} probe={state} />);
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    const second = slotOf({ x: 6, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: first.left + 10, clientY: first.top + 10 });
    fireEvent.pointerMove(window, { clientX: second.left + second.width - 6, clientY: second.top + 10 });
    fireEvent.pointerUp(window);
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha", "gamma"]);
    expect(offered.map((entry) => entry.title)).toEqual(["Moved Alpha"]);
    // Longer than a toast that only says something: the hand has to get to it.
    expect(offered[0]?.timeout).toBeGreaterThan(5000);
    act(() => {
      offered[0]?.undo?.();
    });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta", "gamma"]);
    expect(said.at(-1)).toBe("Change to Alpha undone.");
  });

  it("offers nothing for a press that moved nothing", () => {
    render(<Harness start={[item("alpha", "medium"), item("beta", "medium")]} />);
    const first = slotOf({ x: 0, y: 0, w: 6, h: 2 });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Move Alpha" }), { pointerType: "mouse", button: 0, clientX: first.left + 10, clientY: first.top + 10 });
    fireEvent.pointerUp(window);
    expect(offered).toHaveLength(0);
  });

  it("offers to undo a resize by a grip, from the menu and with the keyboard, each by its own words", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} probe={state} />);
    // By a grip.
    const grip = document.querySelector<HTMLElement>("[data-cell=alpha] [data-resize=corner]");
    if (grip === null) throw new Error("no corner");
    fireEvent.pointerDown(grip, { pointerType: "mouse", button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 100 + 3 * (60 + GAP_PX), clientY: 100 });
    fireEvent.pointerUp(window);
    expect(state.layout()[0]?.size).toBe("medium");
    expect(offered.at(-1)?.title).toBe("Resized Alpha");
    act(() => {
      offered.at(-1)?.undo?.();
    });
    expect(state.layout()[0]?.size).toBe("small");
    // From the menu.
    await user.click(screen.getByRole("button", { name: "Size and position of Beta: Small" }));
    await user.click(await screen.findByRole("menuitem", { name: "Wide" }));
    expect(state.layout()[1]?.size).toBe("wide");
    expect(offered.at(-1)?.title).toBe("Resized Beta");
    act(() => {
      offered.at(-1)?.undo?.();
    });
    expect(state.layout()[1]?.size).toBe("small");
  });

  it("offers to undo a move from the menu", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} probe={state} />);
    await user.click(screen.getByRole("button", { name: "Size and position of Alpha: Small" }));
    await user.click(await screen.findByRole("menuitem", { name: "Move later" }));
    expect(state.layout().map((p) => p.id)).toEqual(["beta", "alpha"]);
    expect(offered.at(-1)?.title).toBe("Moved Alpha");
    act(() => {
      offered.at(-1)?.undo?.();
    });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta"]);
  });

  it("offers to bring back a widget that was taken away, in the place it was, and gives the keyboard to it", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small"), item("gamma", "small")]} probe={state} />);
    await user.click(screen.getByRole("button", { name: "Remove Beta" }));
    await waitFor(() => expect(state.layout().map((p) => p.id)).toEqual(["alpha", "gamma"]));
    await waitFor(() => expect(offered.at(-1)?.title).toBe("Removed Beta"));
    act(() => {
      offered.at(-1)?.undo?.();
    });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta", "gamma"]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Beta" })).toHaveFocus());
  });

  it("says nothing for each step of the keyboard, and offers one undo when the widget is dropped", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small"), item("gamma", "small")]} probe={state} />);
    screen.getByRole("button", { name: "Move Alpha" }).focus();
    await user.keyboard(" {ArrowRight}{ArrowRight}");
    expect(offered).toHaveLength(0);
    await user.keyboard(" ");
    expect(offered.map((entry) => entry.title)).toEqual(["Moved Alpha"]);
    act(() => {
      offered[0]?.undo?.();
    });
    expect(state.layout().map((p) => p.id)).toEqual(["alpha", "beta", "gamma"]);
  });
});

describe("the size with the keyboard", () => {
  it("makes a lifted widget wider, narrower, taller and shorter with Shift and the arrows, saying each size", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small")]} probe={state} />);
    screen.getByRole("button", { name: "Move Alpha" }).focus();
    await user.keyboard(" ");
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(state.layout()[0]?.size).toBe("medium");
    expect(said.at(-1)).toBe("Alpha: size medium.");
    await user.keyboard("{Shift>}{ArrowDown}{/Shift}");
    expect(state.layout()[0]?.size).toBe("large");
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(state.layout()[0]?.size).toBe("xlarge");
    await user.keyboard("{Shift>}{ArrowUp}{/Shift}");
    expect(state.layout()[0]?.size).toBe("wide");
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(state.layout()[0]?.size).toBe("medium");
    // Still lifted, and still where it was in the order: Shift is the size and the arrows alone are the place.
    expect(screen.getByRole("button", { name: "Move Alpha" })).toHaveAttribute("aria-pressed", "true");
  });

  it("says when there is no size that way, and changes nothing", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("delta", "small")]} probe={state} />);
    screen.getByRole("button", { name: "Move Delta" }).focus();
    await user.keyboard(" ");
    for (const [key, words] of [
      ["{ArrowRight}", "Delta has no wider size."],
      ["{ArrowLeft}", "Delta has no narrower size."],
      ["{ArrowDown}", "Delta has no taller size."],
      ["{ArrowUp}", "Delta has no shorter size."],
    ] as const) {
      await user.keyboard(`{Shift>}${key}{/Shift}`);
      expect(said.at(-1)).toBe(words);
    }
    expect(state.layout()[0]?.size).toBe("small");
  });

  it("does nothing with Shift and the arrows on a widget that is not lifted", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small")]} probe={state} />);
    screen.getByRole("button", { name: "Move Alpha" }).focus();
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(state.layout()[0]?.size).toBe("small");
  });

  it("puts the size back too with Escape, and offers one undo for a drop that changed it", async () => {
    const user = userEvent.setup();
    const state = probe();
    render(<Harness start={[item("alpha", "small"), item("beta", "small")]} probe={state} />);
    const handle = screen.getByRole("button", { name: "Move Alpha" });
    handle.focus();
    await user.keyboard(" {Shift>}{ArrowRight}{/Shift}{Escape}");
    expect(state.layout()[0]?.size).toBe("small");
    await user.keyboard(" {Shift>}{ArrowRight}{/Shift} ");
    expect(state.layout()[0]?.size).toBe("medium");
    expect(offered.map((entry) => entry.title)).toEqual(["Resized Alpha"]);
    act(() => {
      offered[0]?.undo?.();
    });
    expect(state.layout()[0]?.size).toBe("small");
  });

  it("is described to the handle, so a screen reader hears that Shift changes the size", () => {
    render(<Harness start={[item("alpha", "small")]} />);
    const help = screen.getByRole("button", { name: "Move Alpha" }).getAttribute("aria-describedby");
    expect(document.getElementById(help ?? "")).toHaveTextContent("change its size with Shift and the arrow keys");
  });
});
