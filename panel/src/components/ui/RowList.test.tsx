import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { RowMenu } from "../page/RowMenu";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import { MenuItem } from "./Menu";
import { RowList } from "./RowList";
import { SkeletonRows } from "./Skeleton";
import { StatusGlyph } from "./StatusPill";

describe("RowList", () => {
  it("is a list named for what it lists, with one row for each item", () => {
    render(
      <RowList aria-label="Environment variables">
        <RowList.Item>DATABASE_URL</RowList.Item>
        <RowList.Item>SECRET_KEY</RowList.Item>
      </RowList>,
    );
    const list = screen.getByRole("list", { name: "Environment variables" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  });

  it("is a bordered surface of one radius, its rows told apart by a hairline", () => {
    render(
      <RowList aria-label="Mounts">
        <RowList.Item>One</RowList.Item>
      </RowList>,
    );
    const list = screen.getByRole("list");
    expect(list).toHaveClass("rounded-card", "border", "border-border", "divide-y", "divide-border", "bg-surface");
    expect(list.className).not.toContain("rounded-control");
  });

  it("makes a row 44px at least and pads it as the card does: 16 across and 12 above and below", () => {
    render(
      <RowList aria-label="Mounts">
        <RowList.Item>One</RowList.Item>
      </RowList>,
    );
    expect(screen.getByRole("listitem")).toHaveClass("min-h-11", "px-4", "py-3");
  });

  it("puts what the row is of at the start and its actions at the end, and the content between", () => {
    render(
      <RowList aria-label="Processes">
        <RowList.Item leading={<StatusGlyph state="running" />} actions={<Button size="sm">Stop</Button>}>
          worker
        </RowList.Item>
      </RowList>,
    );
    const row = screen.getByRole("listitem");
    const [leading, content, actions] = [...row.children];
    expect(leading?.querySelector("svg")).not.toBeNull();
    expect(content).toHaveTextContent("worker");
    expect(content).toHaveClass("flex-1");
    expect(actions).toContainElement(screen.getByRole("button", { name: "Stop" }));
    // A small button is taller than the line of text; the row keeps the height of the text.
    expect(actions).toHaveClass("-my-1");
  });

  it("takes a menu at the end whose name says which row it is", () => {
    render(
      <RowList aria-label="Variables">
        <RowList.Item actions={<RowMenu name="API_KEY"><MenuItem>Edit</MenuItem></RowMenu>}>API_KEY</RowList.Item>
        <RowList.Item actions={<RowMenu name="DATABASE_URL"><MenuItem>Edit</MenuItem></RowMenu>}>DATABASE_URL</RowList.Item>
      </RowList>,
    );
    expect(screen.getByRole("button", { name: "Actions for API_KEY" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Actions for DATABASE_URL" })).toBeInTheDocument();
  });

  it("requires a name, which the type checks", () => {
    // @ts-expect-error A list without an accessible name does not compile.
    const nameless = <RowList>{null}</RowList>;
    expect(nameless).toBeDefined();
  });

  describe("its other states are the kit's, not its own", () => {
    it("has a skeleton of the same frame and the same row height", () => {
      const { container } = render(<SkeletonRows rows={3} />);
      const frame = container.firstElementChild;
      expect(frame).toHaveClass("rounded-card", "border", "divide-y", "bg-surface");
      expect(frame?.children).toHaveLength(3);
      for (const row of frame?.children ?? []) expect(row).toHaveClass("min-h-11", "px-4", "py-3");
    });

    it("is empty as an inline empty state, in the place of the list", () => {
      render(<EmptyState variant="inline" title="No variables yet." />);
      expect(screen.getByText("No variables yet.")).toBeInTheDocument();
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <RowList aria-label="Variables">
        <RowList.Item leading={<StatusGlyph state="running" />} actions={<RowMenu name="API_KEY"><MenuItem>Edit</MenuItem></RowMenu>}>
          API_KEY
        </RowList.Item>
        <RowList.Item>DATABASE_URL</RowList.Item>
      </RowList>,
    );
    await expectNoAxeViolations(container);
  });
});
