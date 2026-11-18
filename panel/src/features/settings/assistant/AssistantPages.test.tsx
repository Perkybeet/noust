/**
 * The assistant's two pages in Settings, read with the permissions the session gives and the sentences the server says: why there is no
 * assistant for this person (never worked out by the console), and the month's spend for whoever reads the settings though they do not
 * use the assistant (item 171).
 */

import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AssistantSettings } from "../../../api/queries/assistant";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const SETTINGS: AssistantSettings = {
  enabled: true,
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
  private_host: "",
  updated_by: "marta",
};

const TOTALS = { completion_tokens: 3_000, estimated_steps: 0, prompt_tokens: 120_000, spend: 4.5, steps: 40, unpriced_steps: 0 };
const USAGE = { currency: "EUR", limits: SETTINGS.limits, month: TOTALS, month_start: "2026-11-01", notes: [], priced_models: ["tiny-model"], actions_last_hour: null, conversation: null };

/** A person who administers the settings and does not use the assistant: the role that read the month before it could not. */
function administrator(assistant: object) {
  return { ...SESSION, permissions: [...(SESSION.permissions ?? []).filter((permission) => permission !== "assistant.use"), "settings.read", "settings.manage"], assistant };
}

function open(path: string, session: object, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({ ...signedInRoutes(session as never), "GET /api/assistant/settings": () => json(200, SETTINGS), "GET /api/assistant/usage": () => json(200, USAGE), ...extra });
  return { ...renderConsole(path), backend };
}

describe("why there is no assistant for this person", () => {
  const off = { enabled: false, can_use: false, tools: false, reason: { code: "assistant.unavailable.off", params: {}, text: "The assistant is off", hint: "A person with the settings.manage permission can turn it on in the assistant's settings." } };

  it("says the reason the session gives, in its own words, on the page of the assistant", async () => {
    open("/settings/assistant", administrator(off), { "GET /api/assistant/settings": () => json(200, { ...SETTINGS, enabled: false }) });
    expect(await screen.findByText(/The assistant is off/)).toBeInTheDocument();
    expect(screen.getByText(/A person with the settings\.manage permission can turn it on/)).toBeInTheDocument();
  });

  it("says a reason for a person who may not use it, which is theirs and not the machine's", async () => {
    const permission = { enabled: true, can_use: false, tools: true, reason: { code: "assistant.unavailable.permission", params: {}, text: "Your role does not allow using the assistant", hint: "An administrator can give you a role that holds assistant.use." } };
    open("/settings/assistant", administrator(permission), { "GET /api/assistant/settings": () => json(200, { ...SETTINGS, enabled: false }) });
    expect(await screen.findByText("Your role does not allow using the assistant An administrator can give you a role that holds assistant.use.")).toBeInTheDocument();
  });

  it("says nothing of a reason when the session gives none: the assistant is there", async () => {
    open("/settings/assistant", administrator({ enabled: true, can_use: true, tools: true, reason: null, model: "tiny-model", location: "machine", mask_personal: true }));
    await screen.findByText("tiny-model");
    expect(screen.queryByText(/does not allow using the assistant/)).not.toBeInTheDocument();
  });
});

describe("what the month cost, read with the settings", () => {
  const unusable = { enabled: true, can_use: false, tools: true, reason: { code: "assistant.unavailable.permission", params: {}, text: "x", hint: null } };

  it("is read by whoever reads the settings though they do not use the assistant, and the hour's actions are not asked of them", async () => {
    const { backend } = open("/settings/assistant/data", administrator(unusable));
    const section = await screen.findByRole("region", { name: "This month" });
    expect(await within(section).findByText(/4\.5 EUR/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/assistant/usage")).toHaveLength(1);
    expect(backend.callsTo("GET /api/assistant/usage")[0]?.search.get("conversation")).toBeNull();
  });

  it("draws no section, and no error, for whoever sees only some teams: the month is the instance's", async () => {
    const { backend } = open("/settings/assistant/data", administrator(unusable), {
      "GET /api/assistant/usage": () => problem(403, "instance_scope_required", "The month's quota belongs to the instance."),
    });
    await screen.findByRole("heading", { name: "What leaves the server" });
    expect(backend.callsTo("GET /api/assistant/usage")).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "This month" })).not.toBeInTheDocument();
    expect(screen.queryByText(/The month's quota belongs to the instance/)).not.toBeInTheDocument();
  });
});

describe("the address you allowed, and the key that stays with its provider", () => {
  const SERVICE: AssistantSettings = { ...SETTINGS, enabled: true, base_url: "https://api.example.org/v1", has_key: true, location: "eu", model: "big-model", prices: {} };
  const ON = { enabled: true, can_use: true, tools: true, reason: null, model: "big-model", location: "eu", mask_personal: true };

  it("says the host and port that were allowed beside the provider, with what the permission is for", async () => {
    open("/settings/assistant", administrator(ON), { "GET /api/assistant/settings": () => json(200, { ...SETTINGS, private_host: "localhost:11434" }) });
    const facts = await screen.findByRole("region", { name: "The model" });
    expect(within(facts).getByText("Allowed address")).toBeInTheDocument();
    expect(within(facts).getByText("localhost:11434")).toBeInTheDocument();
    expect(within(facts).getByText(/for the assistant and for nothing else/)).toBeInTheDocument();
  });

  it("draws no such line while no address was allowed", async () => {
    open("/settings/assistant", administrator(ON));
    await screen.findByText("tiny-model");
    expect(screen.queryByText("Allowed address")).not.toBeInTheDocument();
  });

  it("warns before saving that the key is for the previous provider, and stops warning once this provider's key is typed", async () => {
    const { user } = open("/settings/assistant?step=model", administrator(ON), { "GET /api/assistant/settings": () => json(200, SERVICE) });
    const address = await screen.findByLabelText("Address");
    expect(screen.getByText(/A key is saved\. Leave this empty to keep it/)).toBeInTheDocument();
    await user.clear(address);
    await user.type(address, "https://api.other.example.net/v1");
    expect(screen.getByText("The saved key is for the previous provider and is not kept for this one: type this provider's key.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Key"), "sk-for-this-one");
    expect(screen.queryByText(/is for the previous provider/)).not.toBeInTheDocument();
  });

  it("does not warn for a change of path only: the key stays", async () => {
    const { user } = open("/settings/assistant?step=model", administrator(ON), { "GET /api/assistant/settings": () => json(200, SERVICE) });
    const address = await screen.findByLabelText("Address");
    await user.clear(address);
    await user.type(address, "https://api.example.org/v2");
    expect(screen.queryByText(/is for the previous provider/)).not.toBeInTheDocument();
    expect(screen.getByText(/A key is saved\. Leave this empty to keep it/)).toBeInTheDocument();
  });

  it("says after saving, as what happened and not as an error, that the key went and the assistant is off", async () => {
    const { user, backend } = open("/settings/assistant?step=model", administrator(ON), {
      "GET /api/assistant/settings": () => json(200, SERVICE),
      "PUT /api/assistant/settings": () => json(200, { ...SERVICE, base_url: "https://api.other.example.net/v1", has_key: false, enabled: false }),
    });
    const address = await screen.findByLabelText("Address");
    await user.clear(address);
    await user.type(address, "https://api.other.example.net/v1");
    await user.click(screen.getByRole("button", { name: "Test the connection" }));
    const said = await screen.findByText("The saved key was for the previous provider");
    expect(said.closest("[data-tone]")).toHaveAttribute("data-tone", "info");
    expect(screen.getByText(/the assistant is off until you give this provider's key\. Nothing failed\./)).toBeInTheDocument();
    expect(screen.queryByText("Could not test the connection")).not.toBeInTheDocument();
    expect(backend.callsTo("PUT /api/assistant/settings")[0]?.body).not.toHaveProperty("api_key");
  });
});
