import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Gallery } from "./Gallery";
import type { GalleryProps } from "./Gallery";
import type { Placed } from "./model";

const IN_PANEL: Placed = { id: "attention", type: "attention", size: "medium", settings: {} };

function show(props: Partial<GalleryProps> = {}) {
  const onAdd = vi.fn();
  const view = render(<Gallery scope="server" can={() => true} layout={[IN_PANEL]} full={false} onAdd={onAdd} {...props} />);
  return { ...view, onAdd };
}

function cardOf(title: string): HTMLElement {
  const card = screen.getByRole("button", { name: `Add ${title}` }).closest<HTMLElement>("li");
  if (card === null) throw new Error(`no card for ${title}`);
  return card;
}

describe("a card of the gallery", () => {
  it("puts the icon, the title and the + in one row, and the description and the sizes under it across the card", () => {
    show();
    const card = cardOf("Needs attention");
    const row = screen.getByRole("button", { name: "Add Needs attention" }).parentElement;
    expect(row).toHaveTextContent("Needs attention");
    expect(row).not.toHaveTextContent("What fails");
    const line = [...card.querySelectorAll("p")].find((paragraph) => paragraph.textContent === "What fails or is about to, one line each");
    expect(line?.parentElement).toBe(card);
    const sizes = card.querySelector("ul[aria-label=Sizes]");
    expect(sizes?.parentElement).toBe(card);
    // Nothing is cut: the description has no clamp and the sizes are drawn.
    expect(line?.className).not.toContain("line-clamp");
    expect(sizes?.className).not.toContain("sr-only");
  });

  it("names every size it takes, for a screen reader, in the order they are offered", () => {
    show();
    const sizes = cardOf("Needs attention").querySelector("ul[aria-label=Sizes]");
    expect([...(sizes?.querySelectorAll("li") ?? [])].map((entry) => entry.textContent)).toEqual(["Medium", "Large", "Wide", "Extra large"]);
  });

  it("checks what the panel already has, and only that", () => {
    show();
    expect(cardOf("Needs attention")).toHaveTextContent("In the panel");
    expect(cardOf("Server status")).not.toHaveTextContent("In the panel");
  });

  it("is positioned all the way down, so what is kept for a screen reader is placed against the card and never against the column, which is as tall as the window and made the page longer", () => {
    show();
    const list = document.querySelector("section")?.closest(".overflow-y-auto");
    expect(list).toHaveClass("relative");
    for (const hidden of document.querySelectorAll(".sr-only")) {
      let anchor: Element | null = hidden.parentElement;
      while (anchor !== null && !anchor.classList.contains("relative")) anchor = anchor.parentElement;
      expect(anchor, "an element kept for a screen reader has no positioned ancestor inside the gallery").not.toBeNull();
      expect(list?.contains(anchor)).toBe(true);
    }
  });

  it("adds with its +, and says what the + adds", () => {
    const { onAdd } = show();
    fireEvent.click(screen.getByRole("button", { name: "Add Server status" }));
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd.mock.calls[0]?.[0]).toMatchObject({ type: "figures" });
  });

  it("is taken hold of by the card, with a mouse or a finger, and not by its +", () => {
    const onStartDrag = vi.fn();
    show({ onStartDrag });
    const card = cardOf("Server status");
    fireEvent.pointerDown(card, { pointerType: "mouse", button: 0 });
    fireEvent.pointerDown(card, { pointerType: "touch", button: 0 });
    expect(onStartDrag).toHaveBeenCalledTimes(2);
    fireEvent.pointerDown(screen.getByRole("button", { name: "Add Server status" }), { pointerType: "mouse", button: 0 });
    expect(onStartDrag).toHaveBeenCalledTimes(2);
    fireEvent.pointerDown(card, { pointerType: "mouse", button: 2 });
    expect(onStartDrag).toHaveBeenCalledTimes(2);
  });

  it("cannot be taken hold of, or added, when the panel holds as many as it may", () => {
    const onStartDrag = vi.fn();
    const { onAdd } = show({ onStartDrag, full: true });
    fireEvent.pointerDown(cardOf("Server status"), { pointerType: "mouse", button: 0 });
    expect(onStartDrag).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Add Server status" })).toBeDisabled();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("has no accessibility violation", async () => {
    const { container } = show();
    await expectNoAxeViolations(container);
  });
});
