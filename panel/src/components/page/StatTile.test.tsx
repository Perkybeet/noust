import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { StatusPill } from "../ui/StatusPill";
import { StatTile } from "./StatTile";

describe("StatTile", () => {
  it("shows the label, the value and its context", () => {
    render(<StatTile label="Current release" value="c07d5e3" mono detail="main, deployed 3m ago" />);
    expect(screen.getByText("Current release")).toBeInTheDocument();
    expect(screen.getByText("c07d5e3")).toHaveClass("mono");
    expect(screen.getByText("main, deployed 3m ago")).toBeInTheDocument();
  });

  it("sets a quantity in the interface face, not mono", () => {
    render(<StatTile label="Applications" value={8} />);
    expect(screen.getByText("8")).not.toHaveClass("mono");
  });

  it("takes an element as its value", () => {
    render(<StatTile label="State" value={<StatusPill state="running" />} />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("lets a long context wrap to a second line, whose room is kept from the first frame", () => {
    const { container } = render(<StatTile label="Disk" value="61%" detail="Full in about 507 days, at the rate of the last 30" detailLines={2} />);
    const detail = container.querySelector("[data-slot='detail']");
    expect(detail).toHaveClass("sm:line-clamp-2", "min-h-8");
    expect(detail).not.toHaveClass("truncate");
  });

  it("keeps a short context to one line by default from 640px, and wraps it on a phone", () => {
    const { container } = render(<StatTile label="Disk" value="61%" detail="Of 80 GiB" />);
    const detail = container.querySelector("[data-slot='detail']");
    expect(detail).toHaveClass("sm:truncate", "break-words");
    expect(detail).not.toHaveClass("truncate");
  });

  it("is busy, and keeps its label, while its reading is on its way", () => {
    const { container, rerender } = render(<StatTile loading label="Disk" value={<span>placeholder</span>} />);
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Disk")).toBeInTheDocument();
    rerender(<StatTile label="Disk" value="61%" />);
    expect(container.firstElementChild).not.toHaveAttribute("aria-busy");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <StatTile label="Current release" value="c07d5e3" mono detail="main" />
        <StatTile label="State" value={<StatusPill state="failed" />} />
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
