/**
 * A question the server does not take because of how many are being answered (item 171): the person's own share, or the whole server's. It is
 * a wait the person is told about in the server's own words, never an error block with a failure's title.
 */

import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "../../api/errors";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import { openPanel, resetPanel } from "./store";
import { waitOf } from "./waiting";

const ON = { enabled: true, can_use: true, tools: true, reason: null, model: "tiny-model", location: "machine", mask_personal: true };

function busy(code: string, detail: string, hint: string) {
  return json(429, { error: "queue_full", detail, hint, fields: null, output: null, coded: { code, params: { limit: 2 } } });
}

function panelWith(refusal: () => Response) {
  return fakeBackend({
    ...signedInRoutes({ ...SESSION, permissions: [...(SESSION.permissions ?? []), "assistant.use"], assistant: ON }),
    "GET /api/assistant/conversations": () => json(200, []),
    "POST /api/assistant/conversations": () => json(200, { id: "c-1", title: "", created_at: "2026-11-17T10:00:00+00:00", updated_at: "2026-11-17T10:00:00+00:00" }),
    "GET /api/assistant/conversations/c-1/turns": () => json(200, []),
    "POST /api/assistant/conversations/c-1/turns": refusal,
  });
}

beforeEach(() => {
  // jsdom lays nothing out and has no scroll of an element: the log of the panel scrolls itself to its end.
  Object.defineProperty(HTMLElement.prototype, "scrollTo", { configurable: true, value: () => undefined });
});

afterEach(() => {
  resetPanel();
});

describe("reading a refusal as a wait", () => {
  it("is a 429 that says one of the two sentences of waiting, by its code", () => {
    const coded = { code: "assistant.turn.account_busy", params: { limit: 2 } };
    expect(waitOf(new ApiError(429, "queue_full", "x", null, null, null, null, null, { coded }))?.code).toBe("assistant.turn.account_busy");
    expect(waitOf(new ApiError(429, "queue_full", "x", null, null, null, null, null, { coded: { code: "assistant.turn.busy", params: {} } }))?.code).toBe("assistant.turn.busy");
  });

  it("is nothing else: another status, another code, or the words of the sentence without its code", () => {
    expect(waitOf(new ApiError(409, "conflict", "x", null, null, null, null, null, { coded: { code: "assistant.turn.account_busy", params: {} } }))).toBeNull();
    expect(waitOf(new ApiError(429, "rate_limited", "x", null, null, null, null, null, { coded: { code: "assistant.unavailable.off", params: {} } }))).toBeNull();
    expect(waitOf(new ApiError(429, "queue_full", "You already have 2 answers being written"))).toBeNull();
    expect(waitOf(new Error("429"))).toBeNull();
  });
});

describe("the panel when a question has to wait", () => {
  it("says it as a wait, with the server's words and its fix, and not as a failure", async () => {
    panelWith(() => busy("assistant.turn.account_busy", "You already have 2 answers being written", "Wait for one to end, or stop one, and ask again."));
    renderConsole("/");
    await screen.findByRole("heading", { level: 1 });
    act(() => {
      openPanel({ page: null, label: null, question: "Why is shop failing?" });
    });
    expect(await screen.findByText("Wait a moment before asking again")).toBeInTheDocument();
    expect(screen.getByText("You already have 2 answers being written")).toBeInTheDocument();
    expect(screen.getByText("Wait for one to end, or stop one, and ask again.")).toBeInTheDocument();
    expect(screen.queryByText("Could not send the question")).not.toBeInTheDocument();
  });

  it("still says any other refusal as a failure of the question", async () => {
    panelWith(() => problem(500, "internal", "The model server did not answer"));
    renderConsole("/");
    await screen.findByRole("heading", { level: 1 });
    act(() => {
      openPanel({ page: null, label: null, question: "Why is shop failing?" });
    });
    expect(await screen.findByText("Could not send the question")).toBeInTheDocument();
    expect(screen.queryByText("Wait a moment before asking again")).not.toBeInTheDocument();
  });
});
