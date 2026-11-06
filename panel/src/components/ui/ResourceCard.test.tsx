import { render, screen, within } from "@testing-library/react";
import { KeyRound } from "lucide-react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { LogoTile } from "./LogoTile";
import { Mono } from "./Mono";
import { ResourceCard } from "./ResourceCard";
import { StatusPill } from "./StatusPill";

describe("ResourceCard", () => {
  it("is an article named by the thing's name, with its state beside it and its facts as pairs", () => {
    render(
      <ResourceCard
        name="production"
        icon={KeyRound}
        status={<StatusPill state="failed" label="Token refused" size="sm" />}
        facts={[
          { label: "Provider", value: "Hetzner Cloud" },
          { label: "Servers", value: "2" },
        ]}
        actions={<Button size="sm">Change the token</Button>}
      />,
    );
    const card = screen.getByRole("article", { name: "production" });
    expect(within(card).getByText("Token refused")).toBeInTheDocument();
    expect(within(card).getByText("Provider").tagName).toBe("DT");
    expect(within(card).getByText("Hetzner Cloud").tagName).toBe("DD");
    expect(within(card).getByRole("button", { name: "Change the token" })).toBeInTheDocument();
  });

  it("names itself with the heading level the page leaves it: 2 directly under the title, 3 under a section's", () => {
    const { rerender } = render(<ResourceCard name="oficina" level={2} />);
    expect(screen.getByRole("heading", { level: 2, name: "oficina" })).toBeInTheDocument();
    rerender(<ResourceCard name="oficina" />);
    expect(screen.getByRole("heading", { level: 3, name: "oficina" })).toBeInTheDocument();
  });

  it("draws the icon of its object bare and decorative, or a real logo in its tile, never both", () => {
    const { container, rerender } = render(<ResourceCard name="acme" icon={KeyRound} />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("[data-slot=logo-tile]")).toBeNull();
    rerender(<ResourceCard name="tienda_prod" logo={<LogoTile logo="postgresql" />} icon={KeyRound} />);
    expect(container.querySelector("[data-slot=logo-tile]")).not.toBeNull();
    expect(container.querySelectorAll("svg[data-slot=logo-mark]")).toHaveLength(1);
    expect(container.querySelectorAll("svg:not([data-slot=logo-mark])")).toHaveLength(0);
  });

  it("does not translate a system identifier used as its name", () => {
    render(<ResourceCard name="tienda_prod" untranslated />);
    expect(screen.getByRole("heading", { name: "tienda_prod" })).toHaveAttribute("translate", "no");
  });

  it("draws no list of facts when it has none, and puts what must sit under them in the card", () => {
    const { container } = render(
      <ResourceCard name="worker-01">
        <p>the provider said: server limit reached</p>
      </ResourceCard>,
    );
    expect(container.querySelector("dl")).toBeNull();
    expect(screen.getByText(/server limit reached/)).toBeInTheDocument();
  });

  it("is a seated slab: a card that is read does not react to the pointer", () => {
    render(<ResourceCard name="acme" />);
    const card = screen.getByRole("article", { name: "acme" });
    expect(card).toHaveClass("elev-seated");
    expect(card.className).not.toContain("hover:");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <ResourceCard
        name="noust-acme"
        icon={KeyRound}
        status={<StatusPill state="running" label="Receives pushes" size="sm" />}
        facts={[
          { label: "Owner", value: "acme" },
          { label: "On", value: <Mono>github.com</Mono> },
        ]}
        actions={<Button size="sm">Sync</Button>}
      />,
    );
    await expectNoAxeViolations(container);
  });
});
