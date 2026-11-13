import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { DiffLines } from "./DiffLines";

const LABELS = { added: "Added line", removed: "Removed line" } as const;
const LINES = [
  { kind: "same", text: "services:" },
  { kind: "del", text: "  web: nginx:1.26" },
  { kind: "add", text: "  web: nginx:1.27" },
  { kind: "same", text: "" },
] as const;

describe("DiffLines", () => {
  it("names the group and says in words which lines were added and which were removed", () => {
    render(<DiffLines lines={LINES} label="Changes to web" labels={LABELS} />);
    const group = screen.getByRole("group", { name: "Changes to web" });
    expect(within(group).getAllByText("Added line")).toHaveLength(1);
    expect(within(group).getAllByText("Removed line")).toHaveLength(1);
    expect(within(group).getByText("web: nginx:1.27", { exact: false })).toBeInTheDocument();
  });

  it("reads the change without its colour: a plus or a minus for each changed line, none for the rest", () => {
    const { container } = render(<DiffLines lines={LINES} label="Changes" labels={LABELS} />);
    const rows = container.querySelectorAll("[role='group'] > div");
    expect(rows).toHaveLength(4);
    expect(rows[0]?.querySelector("svg")).toBeNull();
    expect(rows[1]?.querySelector("svg")).not.toBeNull();
    expect(rows[2]?.querySelector("svg")).not.toBeNull();
    expect(rows[1]?.className).toContain("bg-fail-soft");
    expect(rows[2]?.className).toContain("bg-ok-soft");
  });

  it("keeps an empty line as a line, so the shape of the file is the shape of the lines", () => {
    const { container } = render(<DiffLines lines={LINES} label="Changes" labels={LABELS} />);
    expect(container.querySelectorAll("[role='group'] > div")[3]?.textContent).toBe(" ");
  });

  it("says the words of a plan's file when the page gives none, and its own words when it does", () => {
    const { rerender } = render(<DiffLines lines={LINES} label="Changes" />);
    expect(screen.getAllByText("Added line")).toHaveLength(1);
    expect(screen.getAllByText("Removed line")).toHaveLength(1);
    rerender(<DiffLines lines={LINES} label="Changes" labels={{ added: "Now in the stack", removed: "No longer in the stack" }} />);
    expect(screen.getAllByText("Now in the stack")).toHaveLength(1);
    expect(screen.queryByText("Added line")).toBeNull();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<DiffLines lines={LINES} label="Changes" labels={LABELS} />);
    await expectNoAxeViolations(container);
  });
});
