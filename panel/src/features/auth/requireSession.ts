import type { QueryClient } from "@tanstack/react-query";
import { redirect } from "@tanstack/react-router";

import { sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";

/**
 * What an account still has to do before the console opens: enrol a second factor, then
 * accept the usage notice. The server refuses everything else until then (403 mfa_required,
 * notice_required); the console says so up front instead of failing every request.
 */
export function pendingChecks(session: SessionInfo): boolean {
  return session.mfa_required || (session.notice !== null && session.notice !== undefined);
}

/**
 * The console's `beforeLoad`: no session, no shell; a session with checks pending goes to
 * them first. Either way the operator comes back to the address they asked for.
 */
export async function requireSession(queryClient: QueryClient, href: string): Promise<SessionInfo> {
  const session = await queryClient.query(sessionQuery());
  if (!session.authenticated) {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/login", search: { next: href } });
  }
  if (pendingChecks(session)) {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/welcome", search: { next: href } });
  }
  return session;
}
