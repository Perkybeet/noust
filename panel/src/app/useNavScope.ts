import { useCentral } from "../features/central/central";
import { useHasFleet } from "../nodes/servers";
import { useConsoleContext } from "../nodes/useNode";
import type { Scope } from "./nav";

/**
 * Whose destinations the console shows: a server's, or the fleet's. The fleet's pages are the
 * fleet's. The central's own pages (the account, the accounts, the tokens) are it only on a console
 * that has a fleet, or is a hub, which has no server of its own to show: on a lone server they are
 * that server's pages, and its bar, its shortcuts and its search stay what they were. One answer
 * for the sidebar, the shortcuts and the palette.
 */
export function useNavScope(): Scope {
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const { role } = useCentral();
  if (context.kind === "server") return "server";
  return context.kind === "fleet" || hasFleet || role === "hub" ? "fleet" : "server";
}
