import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { expireSession } from "../../api/client";
import { sessionQuery } from "../../api/queries/auth";
import { useSignInFacts } from "./signInFacts";

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
