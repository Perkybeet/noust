import { act, render, screen } from "@testing-library/react";
import { Boxes } from "lucide-react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";

const COMMAND = "noust create -d example.com -s git@github.com:you/app.git";

describe("EmptyState", () => {
  it("marks each of its two variants, so the route tests can tell an answer of nothing from a wait", () => {
    const { container } = render(
      <>
        <EmptyState variant="firstUse" title="First use" />
        <EmptyState variant="inline" title="Inline" />
      </>,
    );
    expect(container.querySelectorAll('[data-slot="empty-state"]')).toHaveLength(2);
    expect([...container.querySelectorAll('[data-slot="empty-state"]')].map((node) => node.getAttribute("data-variant"))).toEqual(["firstUse", "inline"]);
  });

  it("takes the heading level the page outline needs", () => {
    render(<EmptyState variant="firstUse" title="No applications yet" level={3} />);
    expect(screen.getByRole("heading", { level: 3, name: "No applications yet" })).toBeInTheDocument();
  });

  it("fills the place of a page's content on first use: an h2, the one action and its command", () => {
    const { container } = render(
      <EmptyState
        variant="firstUse"
        icon={<Boxes />}
        title="No applications yet"
        description="Deploy a repository and Noust builds and runs it."
        action={<Button variant="primary">New application</Button>}
        command={COMMAND}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "No applications yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New application" })).toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("data-variant", "firstUse");
    expect(container.firstElementChild).not.toHaveClass("border-dashed");
    expect(screen.getByText(COMMAND)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy command" })).toBeInTheDocument();
  });

  it("is one line inline, with no heading, no icon and no frame", () => {
    const { container } = render(
      <EmptyState variant="inline" icon={<Boxes />} title="No deployments yet." action={<Button size="sm">Deploy</Button>} />,
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.getByText("No deployments yet.")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("data-variant", "inline");
    expect(container.firstElementChild?.className).not.toMatch(/border|py-1[026]/);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <EmptyState variant="firstUse" icon={<Boxes />} title="No applications yet" description="Deploy one." command={COMMAND} />,
    );
    await expectNoAxeViolations(container);
  });

  it("labels the copy button in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(<EmptyState variant="firstUse" title="No applications yet" command={COMMAND} />);
    expect(screen.getByRole("button", { name: "Copiar comando" })).toBeInTheDocument();
  });
});
