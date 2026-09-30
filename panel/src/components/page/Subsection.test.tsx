import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "../ui/Button";
import { Subsection } from "./Subsection";

describe("Subsection", () => {
  it("titles a part of a section or card with the subsection role, an h3", () => {
    render(
      <Subsection title="Startup check" description="Before switching traffic, Noust requests this path.">
        <p>Fields</p>
      </Subsection>,
    );
    const heading = screen.getByRole("heading", { level: 3, name: "Startup check" });
    expect(heading).toHaveClass("title", "text-14");
    expect(screen.getByRole("group", { name: "Startup check" })).toHaveTextContent("Fields");
  });

  it("takes a lower level inside a card and actions aligned with its title", () => {
    render(
      <Subsection title="Recent pushes" level={4} actions={<Button size="sm">Refresh</Button>}>
        <p>List</p>
      </Subsection>,
    );
    expect(screen.getByRole("heading", { level: 4, name: "Recent pushes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <Subsection title="Startup check" description="What the new version must answer.">
        <p>Fields</p>
      </Subsection>,
    );
    await expectNoAxeViolations(container);
  });
});
