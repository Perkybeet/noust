import { useCallback } from "react";

import { useReturnServer } from "../nodes/servers";
import { useConsoleContext } from "../nodes/useNode";
import { isCentralOnlyPath } from "./nodeRoute";

/**
 * On a page of the central's (Settings, the alert rules, the backup destinations) the address carries no server, so a link
 * to a page of a server's own would open this one and the server the person had chosen would be forgotten. This gives the
 * search such a link needs to lead back to that server (the one they were on, which the selector remembers), and leaves
 * every other link as it is: on a server's own page the router already keeps it, and a central's page takes none.
 *
 * @returns A function that gives a link's search with the server in it where it belongs, and `undefined` when the link
 *          is fine as it is.
 */
export function useCentralReturn(): (to: string, search?: Readonly<Record<string, unknown>>) => Record<string, unknown> | undefined {
  const context = useConsoleContext();
  const back = useReturnServer();
  return useCallback(
    (to, search) => {
      if (context.kind !== "central" || back === null) return search === undefined ? undefined : { ...search };
      // A link that already says which server it means (a table of servers opens with none) keeps its word.
      if (search !== undefined && "node" in search) return { ...search };
      if (isCentralOnlyPath(to)) return search === undefined ? undefined : { ...search };
      return { ...search, node: back.node ?? undefined };
    },
    [context.kind, back],
  );
}
