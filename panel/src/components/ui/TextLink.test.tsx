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

describe("TextLink", () => {
  it("is a router link in the accent, inside a sentence at the sentence's size", async () => {
    const { container } = await renderRouted(
      <p>
        Served by <TextLink to="/apps/$domain" params={{ domain: "shop.example.com" }}>shop.example.com</TextLink>.
      </p>,
    );
    const link = screen.getByRole("link", { name: "shop.example.com" });
    expect(link).toHaveAttribute("href", "/apps/shop.example.com");
    expect(link.className).toContain("text-accent-fg");
    expect(link.className).not.toContain("text-13");
    await expectNoAxeViolations(container);
  });

  it("stands on its own at the interface's size", async () => {
    await renderRouted(
      <TextLink to="/apps" size="ui">
        All applications
      </TextLink>,
    );
    expect(screen.getByRole("link", { name: "All applications" }).className).toContain("text-13");
  });

  it("lends its look to links the router does not build", () => {
    expect(textLinkClassName()).toContain("text-accent-fg");
    expect(textLinkClassName("ui", "ml-2")).toMatch(/text-13.*ml-2|ml-2.*text-13/);
  });
});
