import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { describeError } from "../../lib/errors";
import { CREDENTIAL, abortedError, installPasskeyBrowser, pendingError } from "../../test/passkeyBrowser";
import type { Outcome } from "../../test/passkeyBrowser";
import { CeremonyError, PENDING_RETRY_DELAYS_MS, SETTLE_LIMIT_MS, createPasskey, getPasskey, isPendingRefusal, setCeremonyPause } from "./webauthn";

/**
 * The page's one passkey window. The browser is faked as Chrome behaves (`test/passkeyBrowser.ts`): a call that arrives while another is
 * open, or while the last one is still closing, is refused with "OperationError: A request is already pending."; and a ceremony that is
 * aborted settles when the test says so, which is how the order of the calls is proven without waiting for a clock.
 */

const browser = installPasskeyBrowser;

const ASSERTION = { challenge: "AQID" };
const CREATION = { challenge: "AQID", user: { id: "AQ" } };

/** One turn of the event loop: every microtask that was going to run has run, so an absence can be counted. */
const turn = (): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, 0);
  });

let waits: number[] = [];

function pausesWith(web: { finishClosing: () => void }): void {
  setCeremonyPause((milliseconds) => {
    waits.push(milliseconds);
    web.finishClosing();
    return Promise.resolve();
  });
}

beforeEach(() => {
  waits = [];
  vi.stubGlobal("PublicKeyCredential", vi.fn());
});

afterEach(() => {
  setCeremonyPause(null);
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "credentials");
});

describe("one passkey window at a time", () => {
  it("ends the autofill and waits until the browser says it is over before an explicit window opens", async () => {
    const web = browser();
    const caller = new AbortController();
    const autofill = getPasskey(ASSERTION, { mediation: "conditional", signal: caller.signal });
    const autofillEnds = expect(autofill).rejects.toMatchObject({ cancelled: true });
    const explicit = getPasskey(ASSERTION);
    await turn();
    // The explicit window has not reached the browser: the autofill was ended with its own signal, not the caller's, and is not over yet.
    expect(web.calls).toEqual([{ kind: "get", refused: false }]);
    expect(web.windows[0]?.options.signal?.aborted).toBe(true);
    expect(caller.signal.aborted).toBe(false);

    web.windows[0]?.settle({ error: abortedError() });
    await autofillEnds;
    await vi.waitFor(() => expect(web.windows).toHaveLength(2));
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(explicit).resolves.toEqual({ id: "passkey" });
    expect(web.calls.map((call) => call.refused)).toEqual([false, false]);
    expect(web.overlaps).toBe(0);
  });

  it("takes the autofill's place with another autofill the same way", async () => {
    const web = browser();
    const first = getPasskey(ASSERTION, { mediation: "conditional" });
    const firstEnds = expect(first).rejects.toMatchObject({ cancelled: true });
    const second = getPasskey(ASSERTION, { mediation: "conditional" });
    await turn();
    expect(web.windows).toHaveLength(1);
    web.windows[0]?.settle({ error: abortedError() });
    await firstEnds;
    await vi.waitFor(() => expect(web.windows).toHaveLength(2));
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(second).resolves.toEqual({ id: "passkey" });
    expect(web.overlaps).toBe(0);
  });

  it.each([
    ["two creations", (): [Promise<unknown>, Promise<unknown>] => [createPasskey(CREATION), createPasskey(CREATION)]],
    ["a creation and a confirmation", (): [Promise<unknown>, Promise<unknown>] => [createPasskey(CREATION), getPasskey(ASSERTION)]],
    ["a confirmation and a creation", (): [Promise<unknown>, Promise<unknown>] => [getPasskey(ASSERTION), createPasskey(CREATION)]],
  ])("refuses the second of %s asked in the same breath as cancelled, and the browser hears of one", async (_name, ask) => {
    const web = browser();
    const [first, second] = ask();
    await expect(second).rejects.toBeInstanceOf(CeremonyError);
    await expect(second).rejects.toMatchObject({ cancelled: true });
    expect(web.calls).toHaveLength(1);
    web.windows[0]?.settle({ value: CREDENTIAL });
    await expect(first).resolves.toEqual({ id: "passkey" });
    expect(web.calls).toHaveLength(1);
  });

  it("never lets an autofill take the place of an explicit window", async () => {
    const web = browser();
    const explicit = getPasskey(ASSERTION);
    const autofill = getPasskey(ASSERTION, { mediation: "conditional" });
    await expect(autofill).rejects.toMatchObject({ cancelled: true });
    expect(web.calls).toHaveLength(1);
    expect(web.windows[0]?.options.signal?.aborted).toBe(false);
    web.windows[0]?.settle({ value: CREDENTIAL });
    await expect(explicit).resolves.toEqual({ id: "passkey" });
  });

  it("is free again before the caller's own continuation runs, so the next ceremony is the caller's to ask", async () => {
    const web = browser();
    const confirmation = getPasskey(ASSERTION);
    web.windows[0]?.settle({ value: CREDENTIAL });
    await confirmation;
    const creation = createPasskey(CREATION);
    expect(web.calls).toEqual([
      { kind: "get", refused: false },
      { kind: "create", refused: false },
    ]);
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(creation).resolves.toEqual({ id: "passkey" });
  });

  it("goes on after a ceremony the browser refused or the operator closed", async () => {
    const web = browser();
    const closed = getPasskey(ASSERTION);
    web.windows[0]?.settle({ error: new DOMException("The request is not allowed by the user agent.", "NotAllowedError") });
    await expect(closed).rejects.toMatchObject({ cancelled: true, detail: "NotAllowedError: The request is not allowed by the user agent." });
    const next = getPasskey(ASSERTION);
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(next).resolves.toEqual({ id: "passkey" });
  });

  it("does not hold an explicit window for ever behind an autofill that never answers its abort", async () => {
    vi.useFakeTimers();
    const web = browser();
    void getPasskey(ASSERTION, { mediation: "conditional" }).catch(() => undefined);
    const explicit = getPasskey(ASSERTION);
    await vi.advanceTimersByTimeAsync(SETTLE_LIMIT_MS - 1);
    expect(web.calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    // The browser still has the autofill open, so what it says is pending, and the retry is what carries it on.
    expect(web.calls[1]).toEqual({ kind: "get", refused: true });
    web.windows[0]?.settle({ error: abortedError() });
    await vi.advanceTimersByTimeAsync(PENDING_RETRY_DELAYS_MS[0] ?? 0);
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(explicit).resolves.toEqual({ id: "passkey" });
  });
});

describe("the caller's own signal", () => {
  it("still ends an autofill, and frees the window for what comes next", async () => {
    const web = browser({ settlesOnAbort: true });
    const caller = new AbortController();
    const autofill = getPasskey(ASSERTION, { mediation: "conditional", signal: caller.signal });
    const autofillEnds = expect(autofill).rejects.toMatchObject({ cancelled: true, reason: "AbortError" });
    caller.abort();
    await autofillEnds;
    expect(web.windows[0]?.options.signal?.aborted).toBe(true);
    const explicit = getPasskey(ASSERTION);
    expect(web.calls).toHaveLength(2);
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(explicit).resolves.toEqual({ id: "passkey" });
  });

  it("that was already aborted never reaches the browser", async () => {
    const web = browser();
    const caller = new AbortController();
    caller.abort();
    await expect(getPasskey(ASSERTION, { mediation: "conditional", signal: caller.signal })).rejects.toMatchObject({ cancelled: true });
    expect(web.calls).toHaveLength(0);
  });

  it("stops the asking again when it ends while the console waits to ask", async () => {
    const caller = new AbortController();
    let asked = 0;
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        get: () => {
          asked += 1;
          return Promise.reject(pendingError());
        },
      },
    });
    setCeremonyPause((milliseconds) => {
      waits.push(milliseconds);
      // The operator leaves the page while the console is waiting to ask again.
      caller.abort();
      return Promise.resolve();
    });
    await expect(getPasskey(ASSERTION, { mediation: "conditional", signal: caller.signal })).rejects.toMatchObject({ cancelled: true, reason: "AbortError" });
    expect(asked).toBe(1);
    expect(waits).toEqual([250]);
  });
});

describe("a browser that says the last window is still closing", () => {
  it("is asked again after a short wait, and answers: the add-a-passkey flow, a confirmation and then a creation", async () => {
    const web = browser({ closesSlowly: true });
    pausesWith(web);
    const confirmation = getPasskey(ASSERTION);
    web.windows[0]?.settle({ value: CREDENTIAL });
    await confirmation;
    const creation = createPasskey(CREATION);
    await vi.waitFor(() => expect(web.windows).toHaveLength(2));
    web.windows[1]?.settle({ value: CREDENTIAL });
    await expect(creation).resolves.toEqual({ id: "passkey" });
    expect(web.calls).toEqual([
      { kind: "get", refused: false },
      { kind: "create", refused: true },
      { kind: "create", refused: false },
    ]);
    expect(waits).toEqual([250]);
    expect(web.overlaps).toBe(0);
  });

  it("is asked again each time it says pending, and the third answer is the one that counts", async () => {
    setCeremonyPause((milliseconds) => {
      waits.push(milliseconds);
      return Promise.resolve();
    });
    const results: Outcome[] = [{ error: pendingError() }, { error: pendingError() }, { value: CREDENTIAL }];
    let asked = 0;
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        create: () => {
          const next = results[asked];
          asked += 1;
          return next?.error ? Promise.reject(next.error) : Promise.resolve(next?.value ?? null);
        },
      },
    });
    await expect(createPasskey(CREATION)).resolves.toEqual({ id: "passkey" });
    expect(asked).toBe(3);
    expect(waits).toEqual([250, 500]);
  });

  it("gives up after the last wait, says why above the browser's own words, and leaves the window free", async () => {
    let asked = 0;
    setCeremonyPause((milliseconds) => {
      waits.push(milliseconds);
      return Promise.resolve();
    });
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        create: () => {
          asked += 1;
          return Promise.reject(pendingError());
        },
      },
    });
    const failure: unknown = await createPasskey(CREATION).catch((error: unknown) => error);
    expect(asked).toBe(1 + PENDING_RETRY_DELAYS_MS.length);
    expect(waits).toEqual([...PENDING_RETRY_DELAYS_MS]);
    expect(PENDING_RETRY_DELAYS_MS.reduce((sum, wait) => sum + wait, 0)).toBeLessThanOrEqual(2000);
    expect(failure).toBeInstanceOf(CeremonyError);
    expect(failure).toMatchObject({ reason: "OperationError", detail: "OperationError: A request is already pending.", cancelled: false });
    // What the error blocks of the console read: the fix above, the browser's words below, verbatim.
    expect(describeError(failure)).toMatchObject({
      hint: "The browser was still closing another passkey window. Try again.",
      detail: "OperationError: A request is already pending.",
    });
    // Free again for the operator's next try.
    Object.defineProperty(navigator, "credentials", { configurable: true, value: { get: () => Promise.resolve(CREDENTIAL) } });
    await expect(getPasskey(ASSERTION)).resolves.toEqual({ id: "passkey" });
  });

  it("says why in the language of the console", async () => {
    await setLocale("es");
    setCeremonyPause(() => Promise.resolve());
    Object.defineProperty(navigator, "credentials", { configurable: true, value: { create: () => Promise.reject(pendingError()) } });
    const failure: unknown = await createPasskey(CREATION).catch((error: unknown) => error);
    expect(describeError(failure)).toMatchObject({
      hint: "El navegador todavía estaba cerrando otra ventana de llaves de acceso. Vuelve a intentarlo.",
      detail: "OperationError: A request is already pending.",
    });
  });

  it.each([
    ["the operator closing the window", new DOMException("The operation either timed out or was not allowed.", "NotAllowedError")],
    ["another refusal of the same name", new DOMException("An attempt was made to use an object that is not usable.", "InvalidStateError")],
    ["a security error", new DOMException("The relying party ID is not a registrable domain suffix.", "SecurityError")],
  ])("is not asked again after %s", async (_name, refusal) => {
    let asked = 0;
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: {
        get: () => {
          asked += 1;
          return Promise.reject(refusal);
        },
      },
    });
    setCeremonyPause((milliseconds) => {
      waits.push(milliseconds);
      return Promise.resolve();
    });
    const failure: unknown = await getPasskey(ASSERTION).catch((error: unknown) => error);
    expect(failure).toMatchObject({ reason: refusal.name });
    expect(describeError(failure).hint).toBeNull();
    expect(asked).toBe(1);
    expect(waits).toEqual([]);
  });
});

describe("what the console takes for a browser that is still closing a window", () => {
  it.each([
    ["Chrome, Edge and the other Chromium browsers", new DOMException("A request is already pending.", "OperationError"), true],
    ["the same words under the other name", new DOMException("A request is already pending.", "InvalidStateError"), true],
    ["a ceremony that says so in lower case", Object.assign(new Error("a request is still pending"), { name: "OperationError" }), true],
    ["the operator's refusal", new DOMException("The request is not allowed by the user agent.", "NotAllowedError"), false],
    ["a refusal of the same name that says another thing", new DOMException("An attempt was made to use an object that is not usable.", "InvalidStateError"), false],
    ["the word in a refusal of another name", new DOMException("Pending.", "NotAllowedError"), false],
    ["something that is not an error", "A request is already pending.", false],
  ])("%s", (_name, cause, expected) => {
    expect(isPendingRefusal(cause)).toBe(expected);
  });
});
