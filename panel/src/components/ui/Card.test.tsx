import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { Card } from "./Card";
import { Skeleton } from "./Skeleton";

describe("Card", () => {
  it("titles its content with a heading at the requested level", () => {
    render(
      <Card title="Resources" description="Against the limits set for this app" level={2}>
        <p>CPU 38%</p>
      </Card>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Resources" })).toBeInTheDocument();
    expect(screen.getByText("Against the limits set for this app")).toBeInTheDocument();
  });

  it("places actions beside the title and a footer below", () => {
    render(
      <Card title="Deploy on push" actions={<Button>Edit</Button>} footer={<Button>Save</Button>}>
        <p>Webhook</p>
      </Card>,
    );
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toContainElement(screen.getByRole("button", { name: "Save" }));
  });

  it("titles with the subsection role, never a loose semibold", () => {
    render(<Card title="Resources">x</Card>);
    const heading = screen.getByRole("heading", { name: "Resources" });
    expect(heading).toHaveClass("title", "text-14");
    expect(heading).not.toHaveClass("font-semibold");
  });

  it.each([
    ["md", "p-5"],
    ["sm", "p-4"],
  ] as const)("pads its body %s", (padding, utility) => {
    render(
      <Card padding={padding}>
        <p>Body</p>
      </Card>,
    );
    expect(screen.getByText("Body").parentElement).toHaveClass(utility);
  });

  it("renders as the element the context needs: a list item in a list of cards", () => {
    render(
      <ul>
        <Card as="li">
          <p>One</p>
        </Card>
      </ul>,
    );
    expect(screen.getByRole("listitem")).toHaveTextContent("One");
  });

  it("marks a card that is itself a link or button target on hover", () => {
    const { container } = render(<Card interactive>x</Card>);
    expect(container.firstElementChild).toHaveClass("hover:border-border-strong");
  });

  it("uses no alpha on a token for its footer", () => {
    render(<Card footer={<span>Foot</span>}>x</Card>);
    expect(screen.getByText("Foot").parentElement?.className).not.toMatch(/\/\d+/);
  });

  it("says it is busy while what it shows is read, in its body or in an action", () => {
    const { container, rerender } = render(
      <Card loading title="Resources" actions={<Skeleton className="h-6 w-20" />}>
        <Skeleton className="h-4 w-32" />
      </Card>,
    );
    expect(container.firstElementChild).toHaveAttribute("aria-busy", "true");
    // The skeleton in the action sits inside the busy card too.
    for (const node of container.querySelectorAll('[data-slot="skeleton"]')) expect(node.closest('[aria-busy="true"]')).not.toBeNull();
    rerender(<Card title="Resources">Done</Card>);
    expect(container.firstElementChild).not.toHaveAttribute("aria-busy");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <main>
        <Card title="Resources" actions={<Button>Edit limits</Button>} footer={<Button>Save</Button>}>
          <p>CPU 38%</p>
        </Card>
        <Card padding="none">
          <p>Edge to edge</p>
        </Card>
      </main>,
    );
    await expectNoAxeViolations(container);
  });
});
