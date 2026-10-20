import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { TextLink, textLinkClassName } from "./TextLink";

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(<RouterProvider router={router} />);
  await screen.findByRole("main");
  return view;
}

describe("TextLink variant=row", () => {
  it("is the name of a row: the text colour, medium, underlined on hover", async () => {
    await renderRouted(
      <TextLink to="/apps/$domain" params={{ domain: "shop.example.com" }} variant="row">
        shop.example.com
      </TextLink>,
    );
    const link = screen.getByRole("link", { name: "shop.example.com" });
    expect(link).toHaveAttribute("href", "/apps/shop.example.com");
    expect(link).toHaveClass("font-medium", "text-fg", "rounded-chip", "hover:underline", "hover:underline-offset-2");
    expect(link).not.toHaveClass("text-accent-fg");
  });

  it("leaves the accent link as it was", async () => {
    await renderRouted(<TextLink to="/apps">All applications</TextLink>);
    const link = screen.getByRole("link", { name: "All applications" });
    expect(link).toHaveClass("text-accent-fg");
    expect(link).not.toHaveClass("text-fg");
  });

  it("lends its look to a link the router does not build, and takes the interface size", () => {
    expect(textLinkClassName("inline", undefined, "row")).toContain("text-fg");
    expect(textLinkClassName("ui", "truncate", "row")).toMatch(/text-13/);
    expect(textLinkClassName("ui", "truncate", "row")).toContain("truncate");
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <TextLink to="/apps" variant="row">
        Applications
      </TextLink>,
    );
    await expectNoAxeViolations(container);
  });
});
