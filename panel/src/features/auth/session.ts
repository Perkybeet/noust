/**
 * What happens to the console when the session ends underneath it, and where the operator
 * goes back to after signing in again.
 */

import type { QueryClient } from "@tanstack/react-query";

import { configureApi } from "../../api/client";
import type { AppRouter } from "../../app/router";
import { askReason, awaitApproval, dismiss, setInboxNavigator } from "../approvals/store";
import { cancelElevation, elevate } from "./elevation";

/** Pages outside the console that must never be a place to come back to after signing in. */
const NOT_NEXT = ["/login", "/welcome", "/setup", "/invite"];

/**
 * Where to go after signing in: `next` when it is a path of this console, the overview
 * otherwise. Anything that could leave the origin (`//evil.example`, `/\evil`, a scheme) is
 * refused, so a crafted sign-in link cannot bounce the operator to another site.
 */
export function safeNext(next: string | undefined | null): string {
  if (typeof next !== "string" || !next.startsWith("/")) return "/";
  if (next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (NOT_NEXT.some((page) => next === page || next.startsWith(`${page}?`) || next.startsWith(`${page}/`))) return "/";
  return next;
}

/**
 * Wires the API client to the console: a lost session clears every cached answer (they
 * belong to a session that no longer exists) and lands on the sign-in page with a notice and
 * the way back; an action that needs elevation opens "Confirm it's you"; one that needs a
 * second person's approval asks why and waits for the decision.
 *
 * @returns A function that removes the wiring.
 */
export function installSessionHandling(router: AppRouter, queryClient: QueryClient): () => void {
  let redirecting = false;
  setInboxNavigator(() => {
    void router.navigate({ to: "/settings/approvals" });
  });
  const restore = configureApi({
    elevate,
    approvalReason: askReason,
    approval: awaitApproval,
    onSessionExpired: () => {
      cancelElevation();
      dismiss("approval_pending");
      const location = router.latestLocation;
      if (redirecting || location.pathname === "/login") return;
      redirecting = true;
      void queryClient.cancelQueries();
      queryClient.clear();
      void router
        .navigate({ to: "/login", search: { next: location.href, reason: "expired" }, replace: true })
        .finally(() => {
          redirecting = false;
        });
    },
  });
  return () => {
    setInboxNavigator(null);
    restore();
  };
}
