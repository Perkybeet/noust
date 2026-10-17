import type { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import type { RouterHistory } from "@tanstack/react-router";

import { installNodeSource } from "../api/nodeScope";
import { installRouteIds, namespacesForRoutes } from "../i18n/routeNamespaces";
import type { Namespace } from "../i18n/types";
import { rememberServer } from "../nodes/lastServer";
import { routeTree } from "../routeTree.gen";
import { RouteError } from "./ErrorBoundary";
import { nodeFromSearch, nodeRewrite, onNodeDropped } from "./nodeRoute";
import { RoutePending } from "./RoutePending";

/** The ids of the routes a path opens, root first: what the router matches without loading anything. */
function matchedRouteIds(router: Pick<AppRouter, "getMatchedRoutes">, pathname: string): string[] {
  const [matched] = router.getMatchedRoutes(pathname);
  return matched.map((route) => route.id as string);
}

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
    // A route that takes more than a second (the session, a page's code) is no longer a page
    // that looks finished while it waits: a skeleton takes its place. Quicker, nothing blinks.
    defaultPendingComponent: RoutePending,
    defaultPendingMs: 1000,
    ...(history ? { history } : {}),
  });
  installNodeSource(() => nodeFromSearch(router.latestLocation.search));
  // The root route loads the texts of whatever page an address opens; the router knows which.
  installRouteIds((location) => matchedRouteIds(router, location.pathname));
  // An old `/n/web-2/fleet` is the fleet now; the server it named is where the operator was.
  onNodeDropped(rememberServer);
  return router;
}

export type AppRouter = ReturnType<typeof buildRouter>;

/**
 * The namespaces of the catalogs that the address the browser opened will draw, which is what
 * the first paint has to wait for besides the chrome. Routes load the same list themselves
 * (routes/__root.tsx); asking before the first render starts the requests earlier, together with
 * the chrome of the language, instead of after the router has begun.
 */
export function namespacesOfLocation(router: AppRouter): readonly Namespace[] {
  return namespacesForRoutes(matchedRouteIds(router, router.latestLocation.pathname));
}

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
