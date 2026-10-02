import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import {
  PROGGEST_EDGES,
  PROGGEST_LAYERS,
  PROGGEST_LAYERS_BACKEND_DOWN,
  PROGGEST_LOGIN_ROUTE,
  SMALL_EDGES,
  SMALL_LAYERS,
} from "../../dev/flowSample";
import { expectNoAxeViolations } from "../../test/axe";
import { edgeId } from "./flowDiagram.layout";
import { FlowDiagram } from "./FlowDiagram";

/** A browser at the given width, and with or without reduced motion. */
function viewport({ wide = true, reducedMotion = false }: { wide?: boolean; reducedMotion?: boolean } = {}): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: (query.includes("min-width") && wide) || (query.includes("prefers-reduced-motion") && reducedMotion),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

function edge(container: HTMLElement, id: string): Element {
  const found = container.querySelector(`[data-edge="${CSS.escape(id)}"]`);
  if (!found) throw new Error(`no edge ${id}`);
  return found;
}

function animated(container: HTMLElement): Element[] {
  return [...container.querySelectorAll(".animate-flow, .animate-flow-fast")];
}

const UNIQUE_EDGES = new Set(PROGGEST_EDGES.map(edgeId)).size;
const TOTAL_NODES = PROGGEST_LAYERS.reduce((sum, layer) => sum + layer.nodes.length, 0);

describe("FlowDiagram", () => {
  it("draws every element of Proggest's site as a focusable node with a name", () => {
    viewport();
    render(<FlowDiagram label="How proggest.es answers" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} />);
    const figure = screen.getByRole("figure", { name: "How proggest.es answers" });
    const total = PROGGEST_LAYERS.reduce((sum, layer) => sum + layer.nodes.length, 0);
    expect(within(figure).getAllByRole("button", { pressed: false })).toHaveLength(total);
    expect(screen.getByRole("button", { name: "Location /api/v1/auth/login" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Backend 127.0.0.1:3000, NestJS · backend service of proggest.es: Responds" }),
    ).toBeInTheDocument();
    for (const layer of PROGGEST_LAYERS) expect(screen.getByRole("list", { name: layer.title })).toBeInTheDocument();
    const backend = screen.getByRole("button", { name: /^Backend 127\.0\.0\.1:3000/ });
    expect(backend.querySelector("[data-glyph]")).toHaveClass("text-ok");
  });

  it("fits its columns to the box it is given, never narrower than a readable node", () => {
    viewport();
    const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1400);
    const { unmount } = render(<FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} />);
    // (1400 - 2 x 16 of padding - 4 gaps of 48) / 5 columns
    expect(screen.getByRole("button", { name: "Location /api/v1/auth/login" }).style.width).toBe("235px");
    unmount();

    width.mockReturnValue(600);
    render(<FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} />);
    expect(screen.getByRole("button", { name: "Location /api/v1/auth/login" }).style.width).toBe("160px");
  });

  it("draws one connection per edge: faster for TLS, a double line for WebSocket", () => {
    viewport();
    const { container } = render(<FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} />);
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(UNIQUE_EDGES);

    const tls = edge(container, edgeId({ from: "p443", to: "s1" }));
    expect(tls.querySelector(".animate-flow-fast")).not.toBeNull();
    const plain = edge(container, edgeId({ from: "p80", to: "s0" }));
    expect(plain.querySelector(".animate-flow")).not.toBeNull();

    const socket = edge(container, edgeId({ from: "s1/l15", to: "u:nestjs_upstream" }));
    expect(socket).toHaveAttribute("data-websocket");
    expect(socket.querySelectorAll("[data-rail]")).toHaveLength(2);
  });

  it("stops every flow when the operator asks for reduced motion", () => {
    viewport({ reducedMotion: true });
    const { container } = render(
      <FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} highlight={PROGGEST_LOGIN_ROUTE} />,
    );
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(UNIQUE_EDGES);
    expect(animated(container)).toEqual([]);
  });

  it("animates only the highlighted route when one is given", () => {
    viewport();
    const { container } = render(
      <FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} highlight={PROGGEST_LOGIN_ROUTE} />,
    );
    const moving = animated(container).map((path) => path.closest("[data-edge]")?.getAttribute("data-edge"));
    expect(moving.sort()).toEqual(
      [
        edgeId({ from: "p443", to: "s1" }),
        edgeId({ from: "s1", to: "s1/l2" }),
        edgeId({ from: "s1/l2", to: "u:nestjs_upstream" }),
        edgeId({ from: "u:nestjs_upstream", to: "b:3000" }),
      ].sort(),
    );
    expect(screen.getByText(/Highlighted path: 443 · TLS → proggest.es → \/api\/v1\/auth\/login → nestjs_upstream → 127.0.0.1:3000\./)).toBeInTheDocument();
  });

  it("lights a node's whole path while it has focus, and reports it", async () => {
    viewport();
    const onNodeFocus = vi.fn();
    const user = userEvent.setup();
    const { container } = render(
      <FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} onNodeFocus={onNodeFocus} />,
    );
    const location = screen.getByRole("button", { name: "Location /api/v1/auth/login" });
    act(() => {
      location.focus();
    });
    expect(onNodeFocus).toHaveBeenLastCalledWith("s1/l2");
    expect(edge(container, edgeId({ from: "s1/l2", to: "u:nestjs_upstream" }))).toHaveAttribute("data-active");
    expect(edge(container, edgeId({ from: "p443", to: "s1" }))).toHaveAttribute("data-active");
    expect(edge(container, edgeId({ from: "s1", to: "s1/l3" }))).not.toHaveAttribute("data-active");

    await user.tab();
    expect(onNodeFocus).toHaveBeenCalledWith(null);
    fireEvent.pointerEnter(screen.getByRole("button", { name: "Port 80, HTTP · IPv4 and IPv6" }));
    expect(edge(container, edgeId({ from: "p80", to: "s0" }))).toHaveAttribute("data-active");
  });

  it("keeps a path lit when a node is pressed, until it is pressed again or Escape", async () => {
    viewport();
    const user = userEvent.setup();
    const { container } = render(<FlowDiagram label="Site" layers={SMALL_LAYERS} edges={SMALL_EDGES} />);
    const ws = screen.getByRole("button", { name: "Location /ws" });
    await user.click(ws);
    expect(ws).toHaveAttribute("aria-pressed", "true");
    fireEvent.pointerLeave(ws);
    act(() => {
      ws.blur();
    });
    expect(edge(container, edgeId({ from: "s0/l1", to: "b:3000" }))).toHaveAttribute("data-active");
    act(() => {
      ws.focus();
    });
    await user.keyboard("{Escape}");
    expect(ws).toHaveAttribute("aria-pressed", "false");
  });

  it("writes a summary that names what does not respond", () => {
    viewport();
    render(<FlowDiagram label="Site" layers={PROGGEST_LAYERS_BACKEND_DOWN} edges={PROGGEST_EDGES} />);
    expect(screen.getByText(new RegExp(`${String(UNIQUE_EDGES)} connections between ${String(TOTAL_NODES)} elements in 5 columns\\.`))).toBeInTheDocument();
    expect(screen.getByText(/1 element is not responding: 127\.0\.0\.1:3000\./)).toBeInTheDocument();
  });

  it("uses the caller's summary when there is one", () => {
    viewport();
    render(<FlowDiagram label="Site" layers={SMALL_LAYERS} edges={SMALL_EDGES} summary="Everything goes to one backend." />);
    expect(screen.getByText("Everything goes to one backend.")).toBeInTheDocument();
  });

  it("offers the same connections as a table", async () => {
    viewport();
    const user = userEvent.setup();
    render(<FlowDiagram label="Site" layers={SMALL_LAYERS} edges={SMALL_EDGES} />);
    await user.click(screen.getByRole("button", { name: "Show the connections as a table" }));
    const table = screen.getByRole("table", { name: "Connections of Site" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(SMALL_EDGES.length);
    expect(within(table).getAllByText("WebSocket over TLS")).toHaveLength(1);
    expect(within(table).getAllByText("Unknown").length).toBeGreaterThan(0);
  });

  it("can leave the table out", () => {
    viewport();
    render(<FlowDiagram label="Site" layers={SMALL_LAYERS} edges={SMALL_EDGES} table={false} />);
    expect(screen.queryByRole("button", { name: "Show the connections as a table" })).not.toBeInTheDocument();
  });

  it("becomes a list per column on a phone, with where each element leads", () => {
    viewport({ wide: false });
    const { container } = render(<FlowDiagram label="Site" layers={SMALL_LAYERS} edges={SMALL_EDGES} />);
    expect(container.querySelector("svg[data-flow-edges]")).toBeNull();
    const locations = screen.getByRole("list", { name: "Locations" });
    expect(within(locations).getByRole("button", { name: "Location /ws" })).toBeInTheDocument();
    const leads = within(locations).getAllByText((_, element) => element?.textContent === "Leads to 127.0.0.1:3000.");
    expect(leads.filter((element) => element.tagName === "SPAN" && element.querySelector("[translate=no]"))).toHaveLength(2);
  });

  it("has no accessibility violations, wide with the table open and on a phone", async () => {
    viewport();
    const user = userEvent.setup();
    const { container, unmount } = render(
      <FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} highlight={PROGGEST_LOGIN_ROUTE} />,
    );
    await user.click(screen.getByRole("button", { name: "Show the connections as a table" }));
    await expectNoAxeViolations(container);
    unmount();

    viewport({ wide: false });
    const phone = render(<FlowDiagram label="Site" layers={PROGGEST_LAYERS} edges={PROGGEST_EDGES} />);
    await expectNoAxeViolations(phone.container);
    // axe over 42 nodes and a 58-row table, twice: seconds, not milliseconds.
  }, 30_000);

  it("speaks Spanish", async () => {
    viewport();
    await act(async () => {
      await setLocale("es");
    });
    render(<FlowDiagram label="Sitio" layers={SMALL_LAYERS} edges={SMALL_EDGES} />);
    expect(screen.getByRole("button", { name: "Backend 127.0.0.1:3000, shop-example-com.service: Desconocido" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ver las conexiones como tabla" })).toBeInTheDocument();
  });
});
