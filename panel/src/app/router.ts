import type { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import type { RouterHistory } from "@tanstack/react-router";

import { installNodeSource } from "../api/nodeScope";
import { rememberServer } from "../nodes/lastServer";
import { routeTree } from "../routeTree.gen";
import { RouteError } from "./ErrorBoundary";
import { nodeFromSearch, nodeRewrite, onNodeDropped } from "./nodeRoute";

/**
 * Builds the router. `history` is for tests; the browser's history is the default.
 *
 * The router is also where the API client learns which server is selected: from the location
 * being loaded (`latestLocation`), so a route's loader already reads the server it is about.
 */
export function buildRouter(queryClient: QueryClient, history?: RouterHistory) {
  const router = createRouter({
    routeTree,
    // `/n/web-2/apps` is `/apps` on node web-2: see nodeRoute.ts.
    rewrite: nodeRewrite,
    context: { queryClient },
    defaultPreload: "intent",
    // TanStack Query owns freshness; the router must not keep its own copy of loader results.
    defaultPreloadStaleTime: 0,
    scrollRestoration: true,
    defaultErrorComponent: RouteError,
    ...(history ? { history } : {}),
  });
  installNodeSource(() => nodeFromSearch(router.latestLocation.search));
  // An old `/n/web-2/fleet` is the fleet now; the server it named is where the operator was.
  onNodeDropped(rememberServer);
  return router;
}

export type AppRouter = ReturnType<typeof buildRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}

/**
 * Builds the router. In development the design gallery is added at /__design; the import
 * sits behind import.meta.env.DEV, so production builds contain neither the route nor the
 * gallery code.
 */
export async function createAppRouter(queryClient: QueryClient): Promise<AppRouter> {
  if (import.meta.env.DEV) {
    const { registerDevRoutes } = await import("../dev/routes");
    registerDevRoutes(routeTree);
  }
  return buildRouter(queryClient);
}
