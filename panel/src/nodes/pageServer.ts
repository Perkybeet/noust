/**
 * The name of the server a page is about, when the console manages several: what the page
 * header says above the title, and what a confirmation names ("Delete shop.example.com on
 * web-2"). The shell provides it once for every page (app/Shell.tsx), so no page has to
 * remember to: the guard is at the chokepoint, not in each caller.
 *
 * Null when there is only one server (nothing to tell apart), on the fleet's and the central's
 * own pages (they are no one server's), and outside the shell (a component rendered alone).
 * A plain React context, with no query behind it, so the kit can read it anywhere.
 */

import { createContext, useContext } from "react";

export const PageServerContext = createContext<string | null>(null);

/** The server the page on screen is about, when there are several; null otherwise. */
export function usePageServer(): string | null {
  return useContext(PageServerContext);
}

/**
 * The server a page or a dialog names: the one it was given, else the shell's. An empty or
 * null `given` defers to the shell, which knows this server's own name on a fleet.
 */
export function useNamedServer(given: string | null | undefined): string | null {
  const shell = usePageServer();
  return given !== undefined && given !== null && given !== "" ? given : shell;
}
