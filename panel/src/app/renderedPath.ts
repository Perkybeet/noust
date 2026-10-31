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
