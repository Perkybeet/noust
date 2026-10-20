import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { DashboardPage } from "./DashboardPage";

async function renderRouted(ui: ReactNode) {
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  await screen.findByRole("main");
  return view;
}

function slots(container: HTMLElement): string[] {
  const template = container.querySelector("[data-template]");
  return [...(template?.querySelectorAll("[data-slot]") ?? [])].map((node) => node.getAttribute("data-slot") ?? "");
}

describe("DashboardPage with tabs", () => {
  const tabs = (
    <nav aria-label="Overview views">
      <a href="/">Overview</a> <a href="/map">Map</a>
    </nav>
  );

  it("puts the tabs between the header and the dashboard, as every other template does", async () => {
    const { container } = await renderRouted(
      <DashboardPage header={{ title: "Overview" }} tabs={tabs} figures={<p>figures</p>} attention={<p>attention</p>} activity={<p>activity</p>} charts={<p>charts</p>} />,
    );
    expect(slots(container)).toEqual(["header", "tabs", "figures", "attention", "activity", "charts"]);
  });

  it("draws the body of a tab that is not the dashboard in the place of the dashboard", async () => {
    const { container } = await renderRouted(
      <DashboardPage header={{ title: "Overview" }} tabs={tabs}>
        <p>The map</p>
      </DashboardPage>,
    );
    expect(slots(container)).toEqual(["header", "tabs", "content"]);
    expect(screen.getByText("The map")).toBeInTheDocument();
  });

  it("lets that body win over the dashboard's slots, which a tab would never fill at once", async () => {
    const { container } = await renderRouted(
      <DashboardPage header={{ title: "Overview" }} tabs={tabs} figures={<p>figures</p>} charts={<p>charts</p>}>
        <p>The map</p>
      </DashboardPage>,
    );
    expect(slots(container)).toEqual(["header", "tabs", "content"]);
    expect(screen.queryByText("figures")).not.toBeInTheDocument();
  });

  it("keeps the first steps in place of everything below the tabs on an empty server", async () => {
    const { container } = await renderRouted(<DashboardPage header={{ title: "Overview" }} tabs={tabs} firstSteps={<p>Deploy your first application</p>} />);
    expect(slots(container)).toEqual(["header", "tabs", "firstSteps"]);
  });

  it("is as it was without tabs", async () => {
    const { container } = await renderRouted(<DashboardPage header={{ title: "Overview" }} figures={<p>figures</p>} />);
    expect(slots(container)).toEqual(["header", "figures"]);
  });

  it("has no accessibility violations", async () => {
    const { container } = await renderRouted(
      <DashboardPage header={{ title: "Overview" }} tabs={tabs}>
        <p>The map</p>
      </DashboardPage>,
    );
    await expectNoAxeViolations(container);
  });
});
