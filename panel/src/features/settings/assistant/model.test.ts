import { describe, expect, it } from "vitest";

import type { AssistantSettings } from "../../../api/queries/assistant";
import { LIMIT_KEYS, draftFrom, hasPrice, isOwn, limitsBody, modelBody, modelMissing, originOf, parseNumber, probeState } from "./model";

function settings(overrides: Partial<AssistantSettings> = {}): AssistantSettings {
  return {
    enabled: false,
    configured: true,
    preset: "",
    base_url: "http://127.0.0.1:11434/v1",
    model: "tiny-model",
    has_key: false,
    location: "machine",
    flow_reason: "",
    mask_personal: true,
    limits: { steps_per_turn: 20, input_tokens_per_conversation: 400_000, spend_per_conversation: 2, spend_per_month: 20, actions_per_hour: 10 },
    prices: { "tiny-model": { input_per_million: 0.08, output_per_million: 0.4 } },
    currency: "EUR",
    calls_tools: true,
    updated_by: "marta",
    ...overrides,
  };
}

describe("where the model runs", () => {
  it("is decided by the place the server saved, because a public address is never this machine", () => {
    expect(originOf({ location: "machine" })).toBe("here");
    expect(originOf({ location: "network" })).toBe("network");
    expect(originOf({ location: "eu" })).toBe("service");
    expect(originOf({ location: "unknown" })).toBe("service");
  });

  it("lets only a model of one's own keep emails and addresses in clear", () => {
    expect(isOwn("here")).toBe(true);
    expect(isOwn("network")).toBe(true);
    expect(isOwn("service")).toBe(false);
  });
});

describe("the draft", () => {
  it("starts from what is saved, with the limits and the price of the model as text", () => {
    const draft = draftFrom(settings());
    expect(draft.origin).toBe("here");
    expect(draft.model).toBe("tiny-model");
    expect(draft.limits.steps_per_turn).toBe("20");
    expect(draft.inputPrice).toBe("0.08");
    expect(draft.currency).toBe("EUR");
    expect(draft.apiKey).toBe("");
  });

  it("says what the model step lacks before it is tested", () => {
    expect(modelMissing({ ...draftFrom(settings()), baseUrl: " " })).toBe("address");
    expect(modelMissing({ ...draftFrom(settings()), model: "" })).toBe("address");
    expect(modelMissing(draftFrom(settings()))).toBeNull();
  });
});

describe("what saving a step sends", () => {
  it("never sends a key that was not typed: the API does not give it back and an empty field keeps it", () => {
    const body = modelBody(draftFrom(settings({ has_key: true })), undefined);
    expect(body).not.toHaveProperty("api_key");
    expect(body.probe).toBe(true);
    expect(modelBody({ ...draftFrom(settings()), apiKey: "typed-by-the-person" }, undefined).api_key).toBe("typed-by-the-person");
  });

  it("asks permission to call a host of one's own, and never for a service", () => {
    expect(modelBody({ ...draftFrom(settings()), allowPrivate: true }, undefined).allow_private_host).toBe(true);
    expect(modelBody({ ...draftFrom(settings({ location: "eu" })), allowPrivate: true }, undefined).allow_private_host).toBe(false);
  });

  it("reads a number the way a person writes it, with a comma in Spanish, and nothing else as one", () => {
    expect(parseNumber("0,08")).toBe(0.08);
    expect(parseNumber(" 400000 ")).toBe(400_000);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("a lot")).toBeNull();
  });

  it("sends the five limits as numbers, the model's price when both are written and the currency in capitals", () => {
    const draft = draftFrom(settings());
    const body = limitsBody({ ...draft, limits: { ...draft.limits, steps_per_turn: "12" }, currency: "eur" }, settings());
    expect(Object.keys(body.limits ?? {}).sort()).toEqual([...LIMIT_KEYS].sort());
    expect(body.limits?.["steps_per_turn"]).toBe(12);
    expect(body.prices?.["tiny-model"]).toEqual({ input_per_million: 0.08, output_per_million: 0.4 });
    expect(body.currency).toBe("EUR");
  });

  it("takes a model's price away when one of its two figures is cleared: a half price is no price", () => {
    const draft = { ...draftFrom(settings()), outputPrice: "" };
    expect(limitsBody(draft, settings()).prices).toEqual({});
    expect(hasPrice(draft)).toBe(false);
  });

  it("leaves the prices of the other models as they are", () => {
    const other = settings({ prices: { "tiny-model": { input_per_million: 1, output_per_million: 2 }, "other-model": { input_per_million: 3, output_per_million: 4 } } });
    const body = limitsBody({ ...draftFrom(other), inputPrice: "5", outputPrice: "6" }, other);
    expect(body.prices?.["other-model"]).toEqual({ input_per_million: 3, output_per_million: 4 });
    expect(body.prices?.["tiny-model"]).toEqual({ input_per_million: 5, output_per_million: 6 });
  });
});

describe("what the probe found", () => {
  it("is drawn by the state of each sentence: answering and calling tools are well, a caveat is a warning, and anything else fails", () => {
    expect(probeState("assistant.probe.answers")).toBe("running");
    expect(probeState("assistant.probe.tools_ok")).toBe("running");
    expect(probeState("assistant.probe.no_tools")).toBe("warning");
    expect(probeState("assistant.probe.memory_low")).toBe("warning");
    expect(probeState("assistant.probe.model_missing")).toBe("failed");
    expect(probeState("assistant.provider.unreachable")).toBe("failed");
  });
});
