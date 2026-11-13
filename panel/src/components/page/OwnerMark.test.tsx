import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { act, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { OwnerMark } from "./OwnerMark";

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(<RouterProvider router={router} />);
  await screen.findByRole("main");
  return view;
}

describe("OwnerMark", () => {
  it("names an application's thing by the application, as a link to it", async () => {
    await renderRouted(<OwnerMark kind="app" subject="shop.example.com" target={{ to: "/apps/$domain", params: { domain: "shop.example.com" } }} />);
    const link = screen.getByRole("link", { name: "shop.example.com, Application" });
    expect(link).toHaveAttribute("href", "/apps/shop.example.com");
    expect(link.querySelector("[translate='no']")).not.toBeNull();
  });

  it("names a database engine's thing and says it is an engine to a screen reader", async () => {
    await renderRouted(<OwnerMark kind="database" subject="postgresql@pg16" target={{ to: "/databases" }} />);
    expect(screen.getByRole("link", { name: "postgresql@pg16, Database engine" })).toBeInTheDocument();
  });

  it("draws Noust's own and nobody's as text, not as a link that goes nowhere", () => {
    const { rerender } = render(<OwnerMark kind="noust" subject="buildkit" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Noust")).toBeInTheDocument();
    rerender(<OwnerMark kind="nobody" subject="" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Noust did not create it")).toBeInTheDocument();
  });

  it("says in a sentence whose it is, with the owner as the link, for a header or a dialog", async () => {
    await renderRouted(<OwnerMark variant="sentence" kind="app" subject="api.example.org" target={{ to: "/apps/$domain", params: { domain: "api.example.org" } }} />);
    const link = screen.getByRole("link", { name: "api.example.org" });
    expect(link.parentElement).toHaveTextContent("Of the application api.example.org");
  });

  it("says a thing nobody owns in the same sentence form and links nothing", () => {
    render(<OwnerMark variant="sentence" kind="nobody" subject="" />);
    expect(screen.getByText("Noust did not create it")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("speaks Spanish with the glossary's words", async () => {
    await act(() => setLocale("es"));
    render(<OwnerMark kind="nobody" subject="" />);
    expect(screen.getByText("No lo creó Noust")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <ul>
        <li>
          <OwnerMark kind="app" subject="shop.example.com" target={{ to: "/apps/$domain", params: { domain: "shop.example.com" } }} />
        </li>
        <li>
          <OwnerMark kind="noust" subject="buildkit" />
        </li>
        <li>
          <OwnerMark kind="nobody" subject="" />
        </li>
      </ul>,
    );
    await expectNoAxeViolations(container);
  });
});
