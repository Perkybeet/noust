/**
 * What the router loads before it shows a page: the namespaces its routes draw.
 *
 * The map is generated from the source (`scripts/i18n-routes.mjs`, `routeNamespaces.gen.ts`), so a
 * page that starts to draw another feature's texts needs no line written by hand; a test fails
 * when the file is stale. A route the map does not know (a page added in development, a file the
 * generator was not run for) is not guessed: it loads every namespace, which is slow once and
 * right, rather than fast and showing keys.
 */

import type { ParsedLocation } from "@tanstack/react-router";

import { NAMESPACES, areNamespacesLoaded, loadNamespaces } from "./catalogs";
import { ROUTE_NAMESPACES } from "./routeNamespaces.gen";
import type { Locale, Namespace } from "./types";

/** The ids of the routes an address opens, root first. */
export type RouteIdsAt = (location: ParsedLocation) => readonly string[];

let routeIdsAt: RouteIdsAt | undefined;

/**
 * The router says how an address becomes route ids (`app/router.ts`). The root route cannot ask
 * the router itself: its `beforeLoad` would take the type of the matches from a router type that
 * is built from that very route, which the compiler cannot resolve. Installed once per router; in a
 * test, the last one built answers.
 *
 * @param source - What names the routes of an address.
 */
export function installRouteIds(source: RouteIdsAt): void {
  routeIdsAt = source;
}

/**
 * The namespaces the routes of a match draw, layouts included, without repeats.
 *
 * @param routeIds - The ids of the matched routes, root first.
 * @returns The namespaces; every one of them when a route is not in the map.
 */
export function namespacesForRoutes(routeIds: readonly string[]): readonly Namespace[] {
  const wanted = new Set<Namespace>();
  for (const id of routeIds) {
    const declared = ROUTE_NAMESPACES[id];
    if (declared === undefined) return NAMESPACES;
    for (const namespace of declared) wanted.add(namespace);
  }
  return [...wanted];
}

/**
 * Makes sure a language has what the routes of an address draw, so the page is shown in it from
 * its first frame. Returns nothing when it is all here (the common case, and a navigation then does
 * not wait even a tick), and otherwise the request; a chunk that cannot be fetched is logged and
 * the page is shown anyway, in English or with its keys, because a page that does not open is worse.
 *
 * @param locale - The active language.
 * @param location - The address being opened.
 * @returns The pending request, or undefined when nothing is pending.
 */
export function loadRouteNamespaces(locale: Locale, location: ParsedLocation): Promise<void> | undefined {
  if (routeIdsAt === undefined) return undefined;
  const wanted = namespacesForRoutes(routeIdsAt(location));
  if (areNamespacesLoaded(locale, wanted)) return undefined;
  return loadNamespaces(locale, wanted).catch((error: unknown) => {
    console.error(`Could not load the ${locale} texts of this page`, error);
  });
}
