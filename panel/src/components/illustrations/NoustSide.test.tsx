import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { NoustSide } from "./NoustSide";

describe("NoustSide: the noust drawn from the side", () => {
  it("is decoration unless it is given a label", () => {
    const { container, rerender } = render(<NoustSide />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("img")).toBeNull();
    rerender(<NoustSide label="A boat drawn up in a hollow of dry stone" />);
    expect(screen.getByRole("img", { name: "A boat drawn up in a hollow of dry stone" })).toBeInTheDocument();
  });

  it("is drawn in tokens only: no colour of its own, no gradient, no filter, no style, nothing loaded", () => {
    const { container } = render(<NoustSide />);
    const html = container.innerHTML;
    // The policy forbids inline style and a drawing may not carry its own colours, so both themes read it.
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|gradient|filter|<style|\sstyle=|<image|<use|href=|url\(/);
    expect(html).toMatch(/stroke-il-stroke/);
    // Stone is grey: every piece of the drawing is told by the stone tokens and the hull's one accent.
    const fills = new Set([...html.matchAll(/\bfill-([a-z0-9-]+)/g)].map((match) => match[1]));
    expect([...fills].sort()).toEqual(["accent", "bg", "bg-column", "none", "surface", "surface-raised"]);
  });

  it("paints the boat in the accent and nothing else", () => {
    const { container } = render(<NoustSide />);
    const violet = [...container.querySelectorAll("[class*='fill-accent']")];
    // The hull, and no other piece: the accent is the one thing in the drawing that is told apart.
    expect(violet).toHaveLength(1);
    expect(violet[0]).toHaveAttribute("class", expect.stringContaining("stroke-accent-joint"));
    expect(container.querySelectorAll("[class*='stroke-accent']").length).toBeGreaterThan(1);
  });

  it("is laid by hand: every stone has its own outline, and two draws are the same drawing", () => {
    const { container, unmount } = render(<NoustSide />);
    const outlines = [...container.querySelectorAll("polygon")].map((stone) => stone.getAttribute("points"));
    // The far wall, its end wall and the low wall in front of the hull: a wall, not a few stones.
    expect(outlines.length).toBeGreaterThanOrEqual(20);
    expect(new Set(outlines).size).toBe(outlines.length);
    const first = container.innerHTML;
    unmount();
    const again = render(<NoustSide />);
    expect(again.container.innerHTML).toBe(first);
  });

  it("lays every stone inside the picture, and every number it draws is finite", () => {
    const { container } = render(<NoustSide />);
    const svg = container.querySelector("svg");
    const [x0, y0, width, height] = (svg?.getAttribute("viewBox") ?? "").split(" ").map(Number);
    expect([x0, y0]).toEqual([0, 0]);
    for (const stone of container.querySelectorAll("polygon")) {
      for (const pair of (stone.getAttribute("points") ?? "").split(" ")) {
        const [x, y] = pair.split(",").map(Number);
        expect(Number.isFinite(x) && Number.isFinite(y), pair).toBe(true);
        expect(x).toBeGreaterThanOrEqual(0);
        expect(x).toBeLessThanOrEqual(width ?? 0);
        expect(y).toBeGreaterThanOrEqual(0);
        expect(y).toBeLessThanOrEqual(height ?? 0);
      }
    }
    expect(container.innerHTML).not.toMatch(/NaN|undefined|Infinity/);
  });

  it("takes the width it is given and keeps its proportions", () => {
    const { container } = render(<NoustSide className="h-auto w-full" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", "0 0 400 230");
    expect(svg).toHaveClass("w-full", "h-auto");
    expect(svg).toHaveAttribute("focusable", "false");
    // No fixed size of its own: the slab decides how much room it has, on a phone and on a desktop.
    expect(svg).not.toHaveAttribute("width");
    expect(svg).not.toHaveAttribute("height");
  });

  it("has no accessibility violations, drawn or labelled", async () => {
    const { container, rerender } = render(<NoustSide />);
    await expectNoAxeViolations(container);
    rerender(<NoustSide label="A boat drawn up in a hollow of dry stone" />);
    await expectNoAxeViolations(container);
  });
});
