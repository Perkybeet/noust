import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";

/**
 * Whether the signed-in account holds a server-wide permission (`settings.manage`). Only the
 * offer: the server decides again on every call, so a stale answer costs a refusal. False while
 * the session is read, so nothing that changes something is drawn for a moment it should not be.
 */
export function useCan(permission: string): boolean {
  const { data } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  return data?.includes(permission) ?? false;
}
