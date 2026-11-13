import { useRouterState } from "@tanstack/react-router";

/**
 * The path of the page that is drawn, not of the one being loaded. The router's `location` changes the moment a link is
 * pressed, while the page it leads to is still arriving (its code, its texts, its data) and the previous one is still on
 * screen; what the frame draws from the new location, the second column of an application's sections for one, shows up
 * empty beside the old page for as long as the new one takes. The frame follows `resolvedLocation` instead, which moves
 * in the same render as the page.
 *
 * @returns The pathname of what is on screen; the one being loaded before anything has been drawn.
 */
export function useRenderedPathname(): string {
  return useRouterState({ select: (state) => (state.resolvedLocation ?? state.location).pathname });
}

/**
 * The route the page on screen is, as the register knows it: the matched route's own path (`/apps/$domain/logs`), except for the one
 * route that stands for many sections of All servers (`/fleet/$`), which is the address itself (`/fleet/databases/engines`): a section of the
 * column or a step of the breadcrumbs is found by it.
 */
export function leafRoute(state: { matches: readonly { fullPath: string }[]; location: { pathname: string } }): string {
  const leaf = state.matches.at(-1)?.fullPath ?? "";
  return leaf === "/fleet/$" ? state.location.pathname : leaf;
}
