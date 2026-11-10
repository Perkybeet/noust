/**
 * The pending "Confirm your password" request, outside React.
 *
 * The server asks for the password before it sets up an account's first second factor when the
 * session signed in a while ago (403 identity_proof_required, noust.web.api.identity_proof). The
 * API client calls `askPassword()` and waits on the promise before sending the call again with
 * it; <IdentityProofDialog> renders whatever request is pending and settles it. Like "Confirm
 * it's you" it is one request for however many calls ask at once.
 */

import { useSyncExternalStore } from "react";

import { ElevationCancelledError } from "../../api/errors";

interface PendingRequest {
  promise: Promise<string>;
  resolve: (password: string) => void;
  reject: (error: Error) => void;
}

let pending: PendingRequest | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

/** Asks the person for their password. Resolves with it; rejects when they decline. */
export function askPassword(): Promise<string> {
  if (pending) return pending.promise;
  let resolve!: (password: string) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<string>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  pending = { promise, resolve, reject };
  notify();
  return promise;
}

export function givePassword(password: string): void {
  const request = pending;
  pending = null;
  request?.resolve(password);
  notify();
}

/** Declines the pending request: the call that asked for it fails as a cancelled confirmation does. */
export function declinePassword(): void {
  const request = pending;
  pending = null;
  request?.reject(new ElevationCancelledError("identity_proof_cancelled"));
  notify();
}

export function usePasswordAsked(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => pending !== null,
  );
}
