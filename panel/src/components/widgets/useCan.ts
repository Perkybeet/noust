import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";

/**
 * What the signed-in credential holds, as a question: whether it holds a permission. A permission it is not
 * yet known to hold is a no (what a role may not see is absent), so a widget never asks the server for what
 * it would refuse.
 */
export function useCan(): (permission: string) => boolean {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  return (permission) => permissions?.includes(permission) ?? false;
}
