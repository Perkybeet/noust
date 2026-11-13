/**
 * What a person chooses to turn the assistant on, and how it becomes what the API takes. Pure: the steps of the page draw it and
 * the tests exercise it without a browser. The server judges every value (an address that cannot be called, a limit out of range);
 * this only keeps what was typed and says which of it is missing.
 */

import type { AssistantPreset, AssistantSettings, AssistantSettingsBody } from "../../../api/queries/assistant";

/** Where the model runs, as the person puts it: on this machine, on their network, or a service somebody else runs. */
export type Origin = "here" | "network" | "service";

export const ORIGINS: readonly Origin[] = ["here", "network", "service"];

/** The limits, always numbers and never "none", in the order a person reads them. */
export const LIMIT_KEYS = ["steps_per_turn", "input_tokens_per_conversation", "spend_per_conversation", "spend_per_month", "actions_per_hour"] as const;
export type LimitKey = (typeof LIMIT_KEYS)[number];

/** The id of "another address": a service that is not on the short list. */
export const OTHER_ADDRESS = "";

export interface Draft {
  origin: Origin;
  /** A preset's id, or {@link OTHER_ADDRESS}. */
  preset: string;
  baseUrl: string;
  /** What was typed; empty keeps the key that is saved. */
  apiKey: string;
  model: string;
  allowPrivate: boolean;
  /** Where a service keeps what it receives, declared by the person; empty leaves it as unstated. */
  location: string;
  flowReason: string;
  mask: boolean;
  limits: Record<LimitKey, string>;
  inputPrice: string;
  outputPrice: string;
  currency: string;
}

/**
 * The origin a saved configuration is: its place decides, because a public address can never be declared this machine or
 * the network.
 *
 * @param settings - What the server answered.
 * @returns The origin to start the page on.
 */
export function originOf(settings: Pick<AssistantSettings, "location">): Origin {
  if (settings.location === "machine") return "here";
  if (settings.location === "network") return "network";
  return "service";
}

/** A model of one's own, on this machine or the network: the only kind that may have emails and addresses in clear. */
export function isOwn(origin: Origin): boolean {
  return origin !== "service";
}

function text(value: number | undefined): string {
  return value === undefined ? "" : String(value);
}

/**
 * The page's starting point: what is saved, so a person who comes back to change the model changes it and does not start again.
 *
 * @param settings - What the server answered.
 * @returns The draft.
 */
export function draftFrom(settings: AssistantSettings): Draft {
  const price = settings.prices[settings.model];
  const origin = originOf(settings);
  return {
    origin,
    preset: settings.preset,
    baseUrl: settings.base_url,
    apiKey: "",
    model: settings.model,
    allowPrivate: false,
    location: origin === "service" && settings.location !== "unknown" ? settings.location : "",
    flowReason: settings.flow_reason,
    mask: settings.mask_personal,
    limits: Object.fromEntries(LIMIT_KEYS.map((key) => [key, text(settings.limits[key])])) as Record<LimitKey, string>,
    inputPrice: text(price?.input_per_million),
    outputPrice: text(price?.output_per_million),
    currency: settings.currency,
  };
}

/**
 * What the model step still lacks, as a key of the page's texts, or null when it has what it needs to be tested.
 *
 * @param draft - What was typed.
 * @returns The missing piece, or null.
 */
export function modelMissing(draft: Draft): "address" | null {
  return draft.baseUrl.trim() === "" || draft.model.trim() === "" ? "address" : null;
}

/**
 * The body that saves the model step and asks the provider what it can do.
 *
 * @param draft - What was typed.
 * @param preset - The preset chosen, when the service is on the short list.
 * @returns The request body.
 */
export function modelBody(draft: Draft, preset: AssistantPreset | undefined): Partial<AssistantSettingsBody> {
  return {
    ...(preset !== undefined ? { preset: preset.id } : { preset: "" }),
    base_url: draft.baseUrl.trim(),
    model: draft.model.trim(),
    // An empty field keeps the key that is saved; the API never gives it back, so there is nothing to put in it.
    ...(draft.apiKey !== "" ? { api_key: draft.apiKey } : {}),
    allow_private_host: isOwn(draft.origin) && draft.allowPrivate,
    ...(draft.origin === "service" && draft.location !== "" ? { location: draft.location } : {}),
    ...(draft.flowReason.trim() !== "" ? { flow_reason: draft.flowReason.trim() } : {}),
    probe: true,
  };
}

/** A number as a person typed it ("0,08" in Spanish), or null when it is not one. */
export function parseNumber(value: string): number | null {
  const parsed = Number(value.trim().replace(",", "."));
  return value.trim() === "" || !Number.isFinite(parsed) ? null : parsed;
}

/**
 * The limits and the prices as the API takes them; a limit that is not a number is left out (the server keeps the old one and
 * says which is wrong), and a model gets a price only when both of its prices are written.
 *
 * @param draft - What was typed.
 * @param settings - What is saved, whose other prices stay.
 * @returns The part of the body.
 */
export function limitsBody(draft: Draft, settings: Pick<AssistantSettings, "prices">): Partial<AssistantSettingsBody> {
  const limits: Record<string, number> = {};
  for (const key of LIMIT_KEYS) {
    const value = parseNumber(draft.limits[key]);
    if (value !== null) limits[key] = value;
  }
  const input = parseNumber(draft.inputPrice);
  const output = parseNumber(draft.outputPrice);
  const prices = { ...settings.prices };
  if (input !== null && output !== null) prices[draft.model] = { input_per_million: input, output_per_million: output };
  else Reflect.deleteProperty(prices, draft.model);
  return { limits, prices, currency: draft.currency.trim().toUpperCase() || "USD", probe: false };
}

/** Whether a model's price is written: without one the spending has no limit, and the page says so. */
export function hasPrice(draft: Pick<Draft, "inputPrice" | "outputPrice">): boolean {
  return parseNumber(draft.inputPrice) !== null && parseNumber(draft.outputPrice) !== null;
}

/** The state of a sentence of the probe, from its code: it decides the glyph and never the words. */
export function probeState(code: string): "running" | "warning" | "failed" {
  if (code === "assistant.probe.answers" || code === "assistant.probe.tools_ok") return "running";
  if (code === "assistant.probe.no_tools" || code === "assistant.probe.memory_low") return "warning";
  return "failed";
}
