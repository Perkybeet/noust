/**
 * The built-in assistant's settings, its providers by name, its usage and its probe (`/api/assistant/*`, items 171 and 184).
 * What the assistant says and does in a conversation is `features/assistant`; this is where an operator chooses the model,
 * the limits and the prices and turns it on and off. The key is written and never read: the server answers only that there
 * is one and since when.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type AssistantSettings = ResponseOf<"/api/assistant/settings", "get">;
export type AssistantSettingsBody = BodyOf<"/api/assistant/settings", "put">;
export type AssistantPresets = ResponseOf<"/api/assistant/presets", "get">;
export type AssistantPreset = AssistantPresets["presets"][number];
export type AssistantUsage = ResponseOf<"/api/assistant/usage", "get">;
export type AssistantProbe = ResponseOf<"/api/assistant/probe", "post">;

export const assistantKeys = {
  all: ["assistant"] as const,
  settings: ["assistant", "settings"] as const,
  presets: ["assistant", "presets"] as const,
  usage: (conversation: string | null) => ["assistant", "usage", { conversation }] as const,
};

export const assistantSettingsQuery = () =>
  queryOptions({
    queryKey: assistantKeys.settings,
    queryFn: ({ signal }) => request("get", "/api/assistant/settings", { signal }),
  });

export const assistantPresetsQuery = () =>
  queryOptions({
    queryKey: assistantKeys.presets,
    queryFn: ({ signal }) => request("get", "/api/assistant/presets", { signal }),
    // A table of the product: it changes with the version, not while a person looks at it.
    staleTime: 5 * 60_000,
  });

/** What a conversation and the month have used of the limits; the conversation is optional (the page that sets the limits has none). */
export const assistantUsageQuery = (conversation: string | null) =>
  queryOptions({
    queryKey: assistantKeys.usage(conversation),
    queryFn: ({ signal }) => request("get", "/api/assistant/usage", { signal, ...(conversation !== null ? { query: { conversation } } : {}) }),
  });

/** Changes the settings, and asks the provider what it can do when the provider changes. Needs "Confirm it's you". */
export function saveAssistantSettings(body: Partial<AssistantSettingsBody>) {
  // The two switches the API describes as required have a default on the server: said here, so a step that changes one thing says only that.
  return request("put", "/api/assistant/settings", { body: { allow_private_host: false, probe: false, ...body } });
}

/** Asks the chosen provider again what models it has and whether the model calls a tool well. */
export function probeAssistant() {
  return request("post", "/api/assistant/probe", {});
}

/** Turns the assistant off and takes back every token it holds. Needs "Confirm it's you". */
export function turnAssistantOff() {
  return request("post", "/api/assistant/off", {});
}
