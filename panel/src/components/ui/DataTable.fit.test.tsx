import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Column } from "./DataTable";
import { DataTable } from "./DataTable";
import { IconButton } from "./IconButton";

/**
 * A table that does not fit the room its page gives it. The actions at the end of a row are never what is cut (docs/DESIGN.md 6.8):
 * the widest column of text gives way first, with an ellipsis and its value in a `title`, and what still does not fit scrolls inside
 * the table with the actions kept in view. jsdom has no layout, so each test lays the table out itself: a column is as wide as its
 * text, or as the cut its cells were given, and the scroll box is as wide as its columns.
 */

interface Row {
  name: string;
  owner: string;
  image: string;
  port: number;
}

const ROWS: Row[] = [
  { name: "adminer", owner: "Noust did not create it", image: "registry.example.com/library/adminer:4", port: 8080 },
  { name: "wiki", owner: "Noust did not create it", image: "registry.example.com/library/wiki:7", port: 8081 },
];

const COLUMNS: Column<Row>[] = [
  { id: "name", header: "Container", cell: (row) => row.name },
  { id: "owner", header: "Owner", cell: (row) => row.owner },
  { id: "image", header: "Image", cell: (row) => row.image, mono: true },
  { id: "port", header: "Port", cell: (row) => row.port, align: "end", mono: true },
];

const IDS = COLUMNS.map((column) => column.id);

/** What each column would be without a cut, and the width of the cell of actions. */
const NATURAL: Record<string, number> = { name: 140, owner: 200, image: 320, port: 70 };
const ACTIONS = 48;

/** The width the cells of a column were told to keep, or its own. */
function widthOf(id: string): number {
  const cell = document.querySelector<HTMLElement>(`tbody tr:first-child td:nth-child(${String(IDS.indexOf(id) + 1)})`);
  const cut = cell?.style.maxWidth ?? "";
  return Math.min(NATURAL[id] ?? 0, cut === "" ? Infinity : parseFloat(cut));
}

/** Lays the table out in a scroll box of `room` pixels, as a browser would for the cells' own widths and cuts. */
function layOut(room: number): void {
  const total = () => IDS.reduce((sum, id) => sum + widthOf(id), ACTIONS);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const id = this instanceof HTMLElement ? this.dataset["column"] : undefined;
    const width = id === undefined ? 0 : widthOf(id);
    return { width, height: 40, top: 0, left: 0, right: width, bottom: 40, x: 0, y: 0, toJSON: () => ({}) };
  });
  vi.spyOn(Element.prototype, "scrollWidth", "get").mockImplementation(function (this: Element) {
    if (this.getAttribute("role") === "region") return total();
    const id = this instanceof HTMLElement && this.matches("td[data-cut]") ? IDS[Array.prototype.indexOf.call(this.parentElement?.children ?? [], this)] : undefined;
    return id === undefined ? 0 : (NATURAL[id] ?? 0);
  });
  vi.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(function (this: Element) {
    if (this.getAttribute("role") === "region") return room;
    return this instanceof HTMLElement && this.matches("td[data-cut]") ? parseFloat(this.style.maxWidth) : 0;
  });
}

function cells(id: string): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(`tbody td:nth-child(${String(IDS.indexOf(id) + 1)})`)];
}

function table(columns: readonly Column<Row>[] = COLUMNS) {
  return (
    <DataTable
      caption="Containers"
      columns={columns}
      rows={ROWS}
      getRowId={(row) => row.name}
      rowActions={(row) => <IconButton label={`Actions for ${row.name}`} icon={<span>+</span>} />}
    />
  );
}

describe("a DataTable that does not fit its room", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("leaves a table that fits as it is", () => {
    layOut(900);
    render(table());
    expect(document.querySelector("td[data-cut]")).toBeNull();
    expect(screen.getAllByRole("button", { name: /Actions for/ })).toHaveLength(2);
  });

  it("cuts the widest text with an ellipsis and says what it holds in a title, rather than cut the actions", () => {
    // 140 + 200 + 320 + 70 + 48 = 778: 78 px too many.
    layOut(700);
    render(table());
    expect(widthOf("image")).toBe(242);
    for (const cell of cells("image")) {
      expect(cell).toHaveClass("truncate");
      expect(cell).toHaveAttribute("title", cell.textContent);
    }
    // The others keep what they have: the name, the owner and the figures are not what an ellipsis helps.
    expect(cells("name")[0]).not.toHaveAttribute("data-cut");
    expect(cells("owner")[0]).not.toHaveAttribute("data-cut");
    expect(cells("port")[0]).not.toHaveAttribute("data-cut");
  });

  it("goes on to the next column when the widest has given what it can, and never cuts below fourteen characters or the figures", () => {
    // Room for almost nothing: image to 128, then the owner to 128, and then the name, the first column, last of all.
    layOut(400);
    render(table());
    expect(widthOf("image")).toBe(128);
    expect(widthOf("owner")).toBe(128);
    expect(widthOf("name")).toBe(128);
    expect(cells("port")[0]).not.toHaveAttribute("data-cut");
  });

  it("does not cut a column that says it is fixed", () => {
    layOut(700);
    const fixed = COLUMNS.map((column) => (column.id === "image" ? { ...column, fixed: true } : column));
    render(table(fixed));
    expect(cells("image")[0]).not.toHaveAttribute("data-cut");
    // The next widest, the owner, gives way instead.
    expect(widthOf("owner")).toBeLessThan(NATURAL["owner"] ?? 0);
  });

  it("does not cut a column whose cells stack lines of their own: an ellipsis is a line's", () => {
    layOut(700);
    const stacked = COLUMNS.map((column) => (column.id === "image" ? { ...column, stacked: () => true } : column));
    render(table(stacked));
    expect(cells("image")[0]).not.toHaveAttribute("data-cut");
    expect(widthOf("owner")).toBeLessThan(NATURAL["owner"] ?? 0);
  });

  it("keeps the actions of a row in view while the table scrolls inside itself", () => {
    layOut(200);
    render(table());
    const actions = cells("port")[0]?.nextElementSibling;
    expect(actions).toHaveClass("sticky", "right-0");
    expect(screen.getByRole("columnheader", { name: "Actions" })).toHaveClass("sticky", "right-0");
  });

  it("lets go of the cuts when the room grows back", () => {
    layOut(700);
    const { rerender } = render(table());
    expect(cells("image")[0]).toHaveAttribute("data-cut");
    layOut(900);
    // The same rows in a new array are how a refetch arrives; the table measures its whole text again.
    rerender(<DataTable caption="Containers" columns={COLUMNS} rows={[...ROWS]} getRowId={(row) => row.name} rowActions={() => <span>+</span>} />);
    expect(document.querySelector("td[data-cut]")).toBeNull();
  });
});
