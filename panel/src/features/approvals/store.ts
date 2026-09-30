/**
 * The approval the operator is being asked about, outside React.
 *
 * The API client calls `askReason()` when the server wants a reason before it files a call
 * for a second person's approval, and `awaitApproval()` once the call became a request; it
 * waits on the promise and sends the call again when it resolves. <ApprovalDialog> renders
 * whatever is pending and settles it. Every call held this way is also remembered for the
 * tab's lifetime, so the approvals inbox can run it once approved after its page is gone.
 */

import { useSyncExternalStore } from "react";

import { ApprovalPendingError } from "../../api/errors";
import type { ApiError } from "../../api/errors";
import type { ApprovalRequested, HeldCall } from "../../api/client";

export type ApprovalPrompt =
  | { kind: "reason"; call: HeldCall; refusal: ApiError; resolve: (reason: string) => void; reject: (error: Error) => void }
  | { kind: "requested"; requested: ApprovalRequested; resolve: () => void; reject: (error: Error) => void };

let pending: ApprovalPrompt | null = null;
const listeners = new Set<() => void>();
/** Calls held for an approval in this tab, by request id: what "Run it now" sends again. */
const held = new Map<string, HeldCall>();

function notify(): void {
  for (const listener of listeners) listener();
}

function open(prompt: ApprovalPrompt): void {
  // A second call held while one is on screen waits in the inbox; the one on screen stays.
  if (pending !== null) {
    prompt.reject(new ApprovalPendingError(prompt.kind === "reason" ? "approval_cancelled" : "approval_pending", prompt.kind === "requested" ? prompt.requested.id : null));
    return;
  }
  pending = prompt;
  notify();
}

/** Asks why the call is made. Resolves with the reason; rejects when the operator declines. */
export function askReason(call: HeldCall, refusal: ApiError): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    open({ kind: "reason", call, refusal, resolve, reject });
  });
}

/** Waits for a request's decision with the operator. Resolves when they run it, approved. */
export function awaitApproval(requested: ApprovalRequested): Promise<void> {
  held.set(requested.id, requested.call);
  return new Promise<void>((resolve, reject) => {
    open({ kind: "requested", requested, resolve, reject });
  });
}

/** Settles what is on screen; the next prompt (a reason given, then the request) can open. */
export function settle(): void {
  pending = null;
  notify();
}

/** The reason is given: the call goes again with it, and comes back as a request. */
export function giveReason(reason: string): void {
  const prompt = pending;
  if (prompt?.kind !== "reason") return;
  settle();
  prompt.resolve(reason);
}

/** Approved, and the operator runs it now. */
export function runNow(): void {
  const prompt = pending;
  if (prompt?.kind !== "requested") return;
  settle();
  held.delete(prompt.requested.id);
  prompt.resolve();
}

/** Closes the prompt: the call does not run now (it may still, from the inbox). */
export function dismiss(code: "approval_pending" | "approval_cancelled" | "approval_rejected" | "approval_expired"): void {
  const prompt = pending;
  if (prompt === null) return;
  settle();
  prompt.reject(new ApprovalPendingError(code, prompt.kind === "requested" ? prompt.requested.id : null));
}

/** The call a request of this tab holds, to run it from the inbox once approved. */
export function heldCall(id: string | number): HeldCall | null {
  return held.get(String(id)) ?? null;
}

/** Forgets a held call once it ran (or will never run). */
export function forgetHeld(id: string | number): void {
  held.delete(String(id));
}

/** For tests. */
export function resetApprovals(): void {
  pending = null;
  held.clear();
  notify();
}

export function useApprovalPrompt(): ApprovalPrompt | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => pending,
  );
}

let toInbox: (() => void) | null = null;

/** How the dialog (mounted outside the router) reaches the approvals inbox. */
export function setInboxNavigator(navigate: (() => void) | null): void {
  toInbox = navigate;
}

/** Opens the approvals inbox, when the console is up. */
export function openInbox(): void {
  toInbox?.();
}
