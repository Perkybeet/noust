import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Select } from "../ui/Select";
import { FilterBar } from "./FilterBar";

function Harness({ count }: { count?: string }) {
  const [q, setQ] = useState("");
  return (
    <FilterBar
      label="Filter applications"
      search={{ value: q, onChange: setQ, label: "Search applications", placeholder: "Name or domain" }}
      filters={<Select aria-label="State" value="all" onValueChange={() => undefined} options={[{ value: "all", label: "Every state" }]} />}
      {...(count !== undefined ? { count } : {})}
    />
  );
}

describe("FilterBar", () => {
  it("is a search landmark with a search box the / shortcut finds", async () => {
    render(<Harness />);
    const region = screen.getByRole("search", { name: "Filter applications" });
    const box = screen.getByRole("searchbox", { name: "Search applications" });
    expect(region).toContainElement(box);
    expect(box).toHaveAttribute("data-page-search");
    await userEvent.type(box, "shop");
    expect(box).toHaveValue("shop");
  });

  it("gives the search box one fixed width, so every list's bar lines up", () => {
    render(<Harness />);
    const frame = screen.getByRole("searchbox").parentElement;
    expect(frame).toHaveClass("sm:w-search");
  });

  it("says how many rows the filters leave, politely, as they change", () => {
    render(<Harness count="3 of 17 applications" />);
    expect(screen.getByRole("status")).toHaveTextContent("3 of 17 applications");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Harness count="17 applications" />);
    await expectNoAxeViolations(container);
  });
});
