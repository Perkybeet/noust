/**
 * `navigator.credentials` as Chrome behaves with it, for the tests of the passkey ceremonies: a window that stays open until the test
 * settles it, a call that arrives while another is open (or while the last one is still closing) refused with "OperationError: A
 * request is already pending.", and a record of every call, so the order of the windows is proven without waiting for a clock.
 */

import { vi } from "vitest";

export type CeremonyKind = "get" | "create";

export interface Outcome {
  value?: Credential | null;
  error?: DOMException;
}

export interface OpenedWindow {
  kind: CeremonyKind;
  options: CredentialRequestOptions | CredentialCreationOptions;
  settle: (outcome: Outcome) => void;
}

export const CREDENTIAL = { toJSON: () => ({ id: "passkey" }) } as unknown as Credential;

export const pendingError = (): DOMException => new DOMException("A request is already pending.", "OperationError");
export const abortedError = (): DOMException => new DOMException("The operation was aborted.", "AbortError");

/**
 * Installs the fake browser. `settlesOnAbort`: the browser rejects an aborted ceremony at once (false: the test settles it).
 * `closesSlowly`: after a window ends, the browser refuses the next call as pending until `finishClosing` is called, which is what
 * the injected pause of the ceremonies does.
 */
export function installPasskeyBrowser({ settlesOnAbort = false, closesSlowly = false }: { settlesOnAbort?: boolean; closesSlowly?: boolean } = {}) {
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("PublicKeyCredential", vi.fn());
  const windows: OpenedWindow[] = [];
  const calls: { kind: CeremonyKind; refused: boolean }[] = [];
  let open = 0;
  let closing = false;
  let overlaps = 0;
  const begin = (kind: CeremonyKind, options: CredentialRequestOptions | CredentialCreationOptions): Promise<Credential | null> =>
    new Promise((resolve, reject) => {
      if (open > 0) overlaps += 1;
      const refused = open > 0 || closing;
      calls.push({ kind, refused });
      if (refused) {
        reject(pendingError());
        return;
      }
      open += 1;
      const opened: OpenedWindow = {
        kind,
        options,
        settle: (outcome) => {
          open -= 1;
          if (closesSlowly) closing = true;
          if (outcome.error !== undefined) reject(outcome.error);
          else resolve(outcome.value ?? null);
        },
      };
      windows.push(opened);
      if (settlesOnAbort) options.signal?.addEventListener("abort", () => opened.settle({ error: abortedError() }), { once: true });
    });
  Object.defineProperty(navigator, "credentials", {
    configurable: true,
    value: {
      get: (options: CredentialRequestOptions) => begin("get", options),
      create: (options: CredentialCreationOptions) => begin("create", options),
    },
  });
  return {
    windows,
    calls,
    get overlaps() {
      return overlaps;
    },
    finishClosing: () => {
      closing = false;
    },
  };
}

/** Takes the fake browser out again. */
export function removePasskeyBrowser(): void {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "credentials");
}
