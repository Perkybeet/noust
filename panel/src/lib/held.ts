/**
 * An action that did not run because the operator held it, not because anything failed: they
 * closed "Confirm it's you", declined to ask for an approval, or left the request waiting for
 * a second person. Said here once, for every place that reports an action's outcome
 * (`ConfirmDialog`, `reportActionError`, `ErrorBlock`), so none of them calls it a failure.
 */

import { ApprovalPendingError, ElevationCancelledError } from "../api/errors";
import { getLocale } from "../app/locale";
import { toast } from "../components/ui/toast";
import { openInbox } from "../features/approvals/store";
import { translate } from "../i18n";

/**
 * What a held action is: `cancelled` (the operator backed out; nothing to say), `waiting` (a
 * request waits for a second person), `decided` (the request was rejected or expired).
 */
export type Held = { kind: "cancelled"; detail: string } | { kind: "waiting"; approvalId: string | null } | { kind: "decided"; detail: string; hint: string | null };

/** The hold an error stands for, or null when it is a real failure. */
export function heldOf(error: unknown): Held | null {
  if (error instanceof ApprovalPendingError) {
    switch (error.error) {
      case "approval_pending":
        return { kind: "waiting", approvalId: error.approvalId };
      case "approval_rejected":
      case "approval_expired":
        return { kind: "decided", detail: error.detail, hint: error.hint };
      default:
        return { kind: "cancelled", detail: error.detail };
    }
  }
  if (error instanceof ElevationCancelledError) return { kind: "cancelled", detail: error.detail };
  return null;
}

/**
 * Says a held action the way it is, as a neutral toast: that nothing changed for a
 * cancellation, "Waiting for approval" with the way to Approvals, or the decision on the
 * request. (A dialog that stays open after a cancellation says nothing at all: see
 * `ConfirmDialog`.)
 *
 * @returns Whether the error was a hold (and the caller must not report it as a failure).
 */
export function reportHeld(error: unknown): boolean {
  const held = heldOf(error);
  if (held === null) return false;
  const locale = getLocale();
  if (held.kind === "waiting") {
    toast.info(translate(locale, "approvals.held.waitingTitle"), {
      description: translate(locale, "approvals.held.waitingDescription"),
      action: { label: translate(locale, "approvals.held.openApprovals"), onClick: openInbox },
    });
  } else if (held.kind === "decided") {
    toast.info(held.detail, held.hint !== null ? { description: held.hint } : {});
  } else {
    toast.info(held.detail);
  }
  return true;
}
