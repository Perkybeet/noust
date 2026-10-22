import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Badge } from "./Badge";

describe("Badge", () => {
  it("is neutral, or accent for something selected or staged: never a state", () => {
    render(
      <div>
        <Badge>Next.js</Badge>
        <Badge tone="accent">Staged</Badge>
      </div>,
    );
    expect(screen.getByText("Next.js")).toHaveClass("text-fg-muted");
    expect(screen.getByText("Staged")).toHaveClass("text-accent-fg");
  });

  it("sets system values in mono", () => {
    render(<Badge mono>node 22.23.3</Badge>);
    expect(screen.getByText("node 22.23.3")).toHaveClass("mono");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <Badge>Next.js</Badge>
        <Badge tone="accent">Primary domain</Badge>
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
