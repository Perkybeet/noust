import { useHasFleet, useServerList } from "../../nodes/servers";
import { useConsoleContext } from "../../nodes/useNode";

/**
 * The server every page is about, when the console holds a fleet: a confirmation names it, and the
 * second column of a resource says where it runs. None on the fleet's and the central's own pages,
 * which are no one server's, and none on a lone server, where there is nothing to tell apart.
 */
export function usePageServerName(): string | null {
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const { hostname } = useServerList();
  if (!hasFleet || context.kind !== "server") return null;
  return context.node ?? hostname;
}
