import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ColumnLayers, SLIDE_MS } from "./ColumnLayers";

function Column({ layerKey, depth, name, settled }: { layerKey: string; depth: number; name: string; settled?: boolean }) {
  return (
    <ColumnLayers layerKey={layerKey} depth={depth} {...(settled !== undefined ? { settled } : {})}>
      <p>{name}</p>
    </ColumnLayers>
  );
}

const layer = (which: "current" | "leaving") => document.querySelector(`[data-layer="${which}"]`);

afterEach(() => {
  vi.useRealTimers();
});

describe("the column's body when the list changes", () => {
  it("slides forward to a resource, decelerating in and accelerating out, and leaves one list at rest", () => {
    vi.useFakeTimers();
    const view = render(<Column layerKey="apps" depth={0} name="Applications" />);
    expect(layer("leaving")).toBeNull();
    expect(layer("current")?.className).not.toContain("column-enter");

    view.rerender(<Column layerKey="app:shop" depth={1} name="shop.example.com" />);
    // Both lists are there while it moves: the new one enters from the right, the old one leaves, unreachable.
    expect(layer("current")?.className).toContain("column-enter-forward");
    expect(layer("leaving")?.className).toContain("column-exit-forward");
    expect(layer("leaving")).toHaveAttribute("aria-hidden", "true");
    expect(layer("leaving")).toHaveAttribute("inert");
    expect(layer("leaving")).toHaveTextContent("Applications");
    expect(layer("current")).toHaveTextContent("shop.example.com");

    act(() => {
      vi.advanceTimersByTime(SLIDE_MS);
    });
    expect(layer("leaving")).toBeNull();
    expect(layer("current")?.className).not.toContain("column-enter");
    expect(screen.queryByText("Applications")).toBeNull();
  });

  it("changes in place when what decides which list it is arrives, and slides again after it", () => {
    vi.useFakeTimers();
    // Before the console knows whether it holds a fleet, the machine's list is a guess: the real one replaces it without a movement.
    const view = render(<Column layerKey="server" depth={0} name="Server" settled={false} />);
    view.rerender(<Column layerKey="server:this" depth={1} name="This server, in the list" settled />);
    expect(layer("leaving")).toBeNull();
    expect(layer("current")).toHaveTextContent("This server, in the list");
    expect(layer("current")?.className).not.toContain("column-enter");
    // And what the person does next moves as ever.
    view.rerender(<Column layerKey="servers" depth={0} name="Servers" settled />);
    expect(layer("current")?.className).toContain("column-enter-back");
  });

  it("slides back out of a resource the other way", () => {
    vi.useFakeTimers();
    const view = render(<Column layerKey="app:shop" depth={1} name="shop.example.com" />);
    view.rerender(<Column layerKey="apps" depth={0} name="Applications" />);
    expect(layer("current")?.className).toContain("column-enter-back");
    expect(layer("leaving")?.className).toContain("column-exit-back");
  });

  it("changes in place between two lists of one depth: two areas, or two servers", () => {
    const view = render(<Column layerKey="apps" depth={0} name="Applications" />);
    view.rerender(<Column layerKey="logs" depth={0} name="Logs" />);
    expect(layer("leaving")).toBeNull();
    expect(layer("current")?.className).not.toContain("column-enter");
    expect(layer("current")).toHaveTextContent("Logs");
  });

  it("is instant with reduced motion, whichever way the depth goes", () => {
    const matches = (query: string): MediaQueryList =>
      ({ matches: query.includes("prefers-reduced-motion"), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList;
    vi.spyOn(window, "matchMedia").mockImplementation(matches);
    const view = render(<Column layerKey="apps" depth={0} name="Applications" />);
    view.rerender(<Column layerKey="app:shop" depth={1} name="shop.example.com" />);
    expect(layer("leaving")).toBeNull();
    expect(layer("current")?.className).not.toContain("column-enter");
    expect(layer("current")).toHaveTextContent("shop.example.com");
  });

  it("keeps the current list live: what its element reads is drawn again without a slide", () => {
    const view = render(<Column layerKey="apps" depth={0} name="Applications" />);
    view.rerender(<Column layerKey="apps" depth={0} name="Applications (12)" />);
    expect(layer("leaving")).toBeNull();
    expect(layer("current")).toHaveTextContent("Applications (12)");
  });
});
