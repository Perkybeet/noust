import { useQuery } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { redirect } from "@tanstack/react-router";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { expireSession } from "../../api/client";
import { sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import { useSignInFacts } from "./signInFacts";

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

/**
 * Keeps watching once the shell is up: the session query refetches when the tab regains
 * focus, and if the answer is now "not signed in" the operator is sent to sign in with the
 * "session expired" notice instead of meeting a failed request first. It is also where a
 * person hears, once, when they last signed in and what failed since.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const { data } = useQuery(sessionQuery());
  const lost = data !== undefined && !data.authenticated;
  useSignInFacts();
  useEffect(() => {
    if (lost) expireSession();
  }, [lost]);
  return lost ? null : children;
}
