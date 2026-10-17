/**
 * Lines that can be opened (checklist M-40, "view in context"): a click or the arrow keys and Enter,
 * a selection that survives new lines, a click that ends a text selection opens nothing, and the
 * list semantics a screen reader needs. A viewer without `onOpenLine` is the one every other page
 * uses, and is covered by LogViewer.test.tsx.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { LogViewer } from "./LogViewer";
import type { LogLine } from "./LogViewer";

const LINES: LogLine[] = [
  { id: 1, text: "nginx started", ref: "c1" },
  { id: 2, text: "upstream timed out", ref: "c2", level: "error" },
  { id: 3, text: "nginx reloaded", ref: "c3" },
];

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
});

function viewer(onOpenLine = vi.fn(), lines: LogLine[] = LINES) {
  const view = render(<LogViewer lines={lines} follow={false} label="Journal" onOpenLine={onOpenLine} openLineLabel="view it in context" />);
  return { onOpenLine, ...view };
}

describe("a viewer whose lines can be opened", () => {
  it("is a list of lines, named, that says what opening one does", () => {
    viewer();
    const list = screen.getByRole("listbox", { name: "Journal" });
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(list).toHaveAccessibleDescription("Click a line, or move with the arrow keys and press Enter, to view it in context.");
  });

  it("opens the line that is clicked, with what the caller knows it by", async () => {
    const { onOpenLine } = viewer();
    await userEvent.click(screen.getByRole("option", { name: "upstream timed out" }));
    expect(onOpenLine).toHaveBeenCalledOnce();
    expect(onOpenLine).toHaveBeenCalledWith(expect.objectContaining({ id: 2, ref: "c2" }));
    expect(screen.getByRole("option", { name: "upstream timed out" })).toHaveAttribute("aria-selected", "true");
  });

  it("moves through the lines with the arrow keys and opens the selected one with Enter", async () => {
    const { onOpenLine } = viewer();
    const list = screen.getByRole("listbox");
    list.focus();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(list).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "upstream timed out" }).id);
    await userEvent.keyboard("{Enter}");
    expect(onOpenLine).toHaveBeenCalledWith(expect.objectContaining({ ref: "c2" }));

    await userEvent.keyboard("{End}");
    expect(screen.getByRole("option", { name: "nginx reloaded" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: "nginx reloaded" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Home}{ArrowUp}");
    expect(screen.getByRole("option", { name: "nginx started" })).toHaveAttribute("aria-selected", "true");
  });

  it("starts at the newest line when going up from nothing, and opens nothing before a line is selected", async () => {
    const { onOpenLine } = viewer();
    screen.getByRole("listbox").focus();
    await userEvent.keyboard("{Enter}");
    expect(onOpenLine).not.toHaveBeenCalled();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("option", { name: "nginx reloaded" })).toHaveAttribute("aria-selected", "true");
  });

  it("opens nothing when the click ends a text selection, so a line can still be copied", () => {
    const { onOpenLine } = viewer();
    const selection = vi.spyOn(window, "getSelection").mockReturnValue({ toString: () => "timed out" } as Selection);
    fireEvent.click(screen.getByRole("option", { name: "upstream timed out" }));
    expect(onOpenLine).not.toHaveBeenCalled();
    selection.mockRestore();
  });

  it("keeps the selected line when more lines arrive", async () => {
    const { rerender } = viewer();
    await userEvent.click(screen.getByRole("option", { name: "upstream timed out" }));
    rerender(<LogViewer lines={[{ id: 0, text: "older" }, ...LINES]} follow={false} label="Journal" onOpenLine={vi.fn()} />);
    expect(screen.getByRole("option", { name: "upstream timed out" })).toHaveAttribute("aria-selected", "true");
  });

  it("has no accessibility violations", async () => {
    const { container } = viewer();
    await userEvent.click(screen.getByRole("option", { name: "nginx started" }));
    await expectNoAxeViolations(container);
  });

  it("is untouched without onOpenLine: a region, rows nobody can select", () => {
    render(<LogViewer lines={LINES} follow={false} label="Journal" />);
    expect(screen.getByRole("region", { name: "Journal" })).toBeInTheDocument();
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });
});
