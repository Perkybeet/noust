import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { fakeBackend } from "../../test/fakes";
import { FleetPanel } from "./TaskPanel";
import { EMPTY, withFleetItems } from "./store";
import { job, operation } from "./taskFixtures";

const NOW = 1_000_000;

function show(ui: ReactNode) {
  fakeBackend({});
  const root = createRootRoute();
  const index = createRoute({ getParentRoute: () => root, path: "/", component: () => ui });
  const elsewhere = createRoute({ getParentRoute: () => root, path: "$", component: () => null });
  const router = createRouter({ routeTree: root.addChildren([index, elsewhere]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const ROWS = [
  { node: "central-01", local: true, status: "ok", jobs: [job({ id: "c1" })], operations: [operation({ id: "op-c" })] },
  { node: "web-01", local: false, status: "ok", jobs: [job({ id: "w1", progress: 72, title: { code: "jobs.title.deploy", params: { domain: "reservas.example.com" } }, name: "Deploy reservas.example.com" })], operations: [] },
  { node: "web-02", local: false, status: "unsupported", jobs: [], operations: [] },
  { node: "web-03", local: false, status: "unreachable", jobs: [], operations: [] },
  { node: "db-02", local: false, status: "ok", jobs: [], operations: [] },
];

describe("the panel on All servers", () => {
  const state = withFleetItems(EMPTY, ROWS, [{ name: "web-02", version: "4.1.0" }], NOW, NOW - 10);

  it("groups what runs by server, and the central says it is the central", async () => {
    show(<FleetPanel state={state} target="4.2.0" onNavigate={() => undefined} />);
    const central = await screen.findByRole("region", { name: "central-01" });
    expect(within(central).getByText("central")).toBeInTheDocument();
    expect(within(central).getByText("Deploy shop.example.com")).toBeInTheDocument();
    expect(within(central).getByText("Restart api.example.com")).toBeInTheDocument();
    const web01 = screen.getByRole("region", { name: "web-01" });
    expect(within(web01).getByText("Deploy reservas.example.com")).toBeInTheDocument();
    expect(within(web01).getByText("72 %")).toBeInTheDocument();
  });

  it("says a server that has not been updated will count its tasks when it is, with the way to update it", async () => {
    show(<FleetPanel state={state} target="4.2.0" onNavigate={() => undefined} />);
    const old = await screen.findByRole("region", { name: "web-02" });
    expect(old).toHaveTextContent("It has Noust 4.1.0: it will count its tasks here when it is updated to 4.2.0.");
    expect(within(old).getByRole("link", { name: "Update it" })).toHaveAttribute("href", "/fleet/updates");
  });

  it("says a server that cannot be reached cannot be asked, and one with nothing to do has nothing", async () => {
    show(<FleetPanel state={state} target="4.2.0" onNavigate={() => undefined} />);
    expect(within(await screen.findByRole("region", { name: "web-03" })).getByText("No connection: it cannot be asked.")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "db-02" })).getByText("Nothing is running.")).toBeInTheDocument();
  });

  it("waits with a skeleton until the central has answered", async () => {
    show(<FleetPanel state={EMPTY} target="4.2.0" onNavigate={() => undefined} />);
    expect(await screen.findByText("Tasks")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    show(<FleetPanel state={state} target="4.2.0" onNavigate={() => undefined} />);
    await screen.findByRole("region", { name: "web-02" });
    await expectNoAxeViolations(document.body);
  });
});
