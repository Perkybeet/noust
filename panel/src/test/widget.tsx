import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";

import { widgetOf } from "../components/widgets/catalog";
import type { Settings, SizeKey } from "../components/widgets/model";
import type { PanelScope } from "../components/widgets/types";
import { fakeBackend, signedInRoutes } from "./fakes";
import type { RouteHandler } from "./fakes";

export interface RenderWidgetOptions {
  size: SizeKey;
  scope?: PanelScope;
  settings?: Settings;
  /** What the API answers, on top of what every signed-in page needs. */
  routes?: Record<string, RouteHandler>;
}

/**
 * One widget's body, alone, in a cell of the size it asks about, over a fake API and a router that takes any link:
 * what a widget says is tested here at two sizes without a page, a frame or a stream. The cell is a size container
 * like the grid's, though jsdom lays nothing out, so what the room changes is the grid's and the browser's to show.
 */
export function renderWidget(type: string, { size, scope = "server", settings = {}, routes = {} }: RenderWidgetOptions) {
  const definition = widgetOf(type);
  if (definition === undefined) throw new Error(`no widget called ${type}`);
  const backend = fakeBackend({ ...signedInRoutes(), ...routes });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 10_000 }, mutations: { retry: false } } });
  const { Body } = definition;
  const root = createRootRoute();
  const index = createRoute({
    getParentRoute: () => root,
    path: "/",
    component: (): ReactNode => (
      <div data-testid="cell" data-size={size} className="widget-body">
        <Body size={size} scope={scope} settings={settings} />
      </div>
    ),
  });
  // Every page a widget links to: a link here has an address and goes nowhere.
  const elsewhere = createRoute({ getParentRoute: () => root, path: "$", component: () => null });
  const router = createRouter({ routeTree: root.addChildren([index, elsewhere]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, backend, queryClient };
}
