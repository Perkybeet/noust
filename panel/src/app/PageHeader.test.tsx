import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { Button } from "../components/ui/Button";
import { MenuItem } from "../components/ui/Menu";
import { StatusPill } from "../components/ui/StatusPill";
import { expectNoAxeViolations } from "../test/axe";
import { PageHeader } from "./PageHeader";

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/apps/x"] }) });
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { level: 1 });
  return view;
}

describe("PageHeader", () => {
  it("keeps its 3.0 form: title, description, actions", async () => {
    await renderRouted(<PageHeader title="Applications" description="Everything this server runs." actions={<Button>New</Button>} />);
    expect(screen.getByRole("heading", { level: 1, name: "Applications" })).toBeInTheDocument();
    expect(screen.getByText("Everything this server runs.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New" })).toBeInTheDocument();
  });

  it("puts the state beside the title and the facts on a line under it", async () => {
    await renderRouted(
      <PageHeader
        title="shop.example.com"
        mono
        status={<StatusPill state="running" />}
        meta={<span>Next.js · :3001</span>}
        description="The storefront."
      />,
    );
    const heading = screen.getByRole("heading", { level: 1, name: "shop.example.com" });
    expect(heading).toHaveClass("mono");
    expect(heading.parentElement).toHaveTextContent("Running");
    expect(screen.getByText("Next.js · :3001")).toBeInTheDocument();
  });

  it("orders the actions: secondary, then the one primary, then the overflow menu", async () => {
    await renderRouted(
      <PageHeader
        title="shop.example.com"
        secondaryActions={<Button>Restart</Button>}
        primaryAction={<Button variant="primary">Update</Button>}
        overflow={<MenuItem>Stop</MenuItem>}
      />,
    );
    const actions = screen.getByRole("button", { name: "Update" }).parentElement;
    if (!actions) throw new Error("no actions");
    const names = within(actions)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent);
    expect(names).toEqual(["Restart", "Update", "More actions"]);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("menuitem", { name: "Stop" })).toBeInTheDocument();
  });

  it("names the server above the title when the console manages several", async () => {
    await renderRouted(<PageHeader title="Applications" server="web-2" />);
    expect(screen.getByText("web-2")).toHaveAttribute("translate", "no");
    expect(screen.getByText("web-2").parentElement).toHaveTextContent("Server web-2");
  });

  it("leaves its bottom margin to a template when flush", async () => {
    const { container } = await renderRouted(<PageHeader title="Applications" flush />);
    expect(container.querySelector("header")).not.toHaveClass("mb-8");
  });

  describe("place", () => {
    it("says where the resource lives, in mono, above the title, each step a link to the list it filters", async () => {
      await renderRouted(
        <PageHeader
          title="shop.example.com"
          place={[
            { label: "Acme", to: "/apps", search: { team: "acme" } },
            { label: "Web", to: "/apps", search: { team: "acme", project: "web" } },
            { label: "production", to: "/apps", search: { team: "acme", project: "web", environment: "production" } },
          ]}
        />,
      );
      const place = screen.getByRole("group", { name: "Where this lives" });
      expect(within(place).getAllByRole("link").map((link) => link.textContent)).toEqual(["Acme", "Web", "production"]);
      expect(within(place).getByRole("link", { name: "production" })).toHaveAttribute("href", expect.stringContaining("environment=production"));
      expect(within(place).getByText("Acme")).toHaveAttribute("translate", "no");
      expect(place).toHaveTextContent("Acme / Web / production");
      // Above the title, not among the facts under it.
      const heading = screen.getByRole("heading", { level: 1 });
      expect(place.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("follows the server's name when the console manages several", async () => {
      await renderRouted(<PageHeader title="shop.example.com" server="fra-1" place={[{ label: "Acme", to: "/apps" }]} />);
      expect(screen.getByText("fra-1").closest("p")).toHaveTextContent("Server fra-1 · Acme");
    });

    it("draws nothing where there is nothing to tell apart", async () => {
      await renderRouted(<PageHeader title="shop.example.com" place={[]} />);
      expect(screen.queryByRole("group", { name: "Where this lives" })).not.toBeInTheDocument();
    });
  });

  it("puts what else is on the state's line after the state, on the same line as the title", async () => {
    await renderRouted(
      <PageHeader
        title="shop.example.com"
        status={<StatusPill state="running" />}
        statusExtras={<button type="button">3 not applied</button>}
      />,
    );
    const heading = screen.getByRole("heading", { level: 1 });
    const chip = screen.getByRole("button", { name: "3 not applied" });
    expect(chip.parentElement).toBe(heading.parentElement);
    expect(heading.parentElement).toHaveTextContent(/Running\s*3 not applied/);
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <PageHeader
        title="shop.example.com"
        mono
        place={[{ label: "Acme", to: "/apps" }]}
        statusExtras={<button type="button">3 not applied</button>}
        breadcrumbs={[{ label: "Applications", to: "/apps" }]}
        status={<StatusPill state="running" />}
        meta={<span>Next.js</span>}
        secondaryActions={<Button>Restart</Button>}
        primaryAction={<Button variant="primary">Update</Button>}
        overflow={<MenuItem>Stop</MenuItem>}
      />,
    );
    await expectNoAxeViolations(container);
  });
});
