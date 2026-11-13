import { useHasFleet } from "../nodes/servers";
import { useConsoleContext, useSelection } from "../nodes/useNode";
import type { Scope } from "./nav";

/**
 * Whose destinations the console shows: a server's, or the fleet's. A server's pages are that server's and the
 * fleet's pages are the fleet's. A page of the instance (the account, the accounts, the tokens) belongs to no
 * server and leaves the selector where it was, so the bar follows the selector: the fleet's faces when it is on
 * "All servers", the server's pages when it is on a server. On a lone server there is nothing to tell apart and the
 * bar, the shortcuts and the search stay what they were. One answer for the sidebar, the shortcuts and the palette.
 */
export function useNavScope(): Scope {
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const selection = useSelection();
  if (context.kind === "server") return "server";
  if (context.kind === "fleet") return "fleet";
  return hasFleet && selection.kind === "all" ? "fleet" : "server";
}
