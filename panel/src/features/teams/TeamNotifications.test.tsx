import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { teamsRoutes, viewerSession } from "./testFixtures";

vi.setConfig({ testTimeout: 30_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const OPS = {
  name: "ops",
  type: "slack",
  enabled: true,
  configured: true,
  settings: { webhook_url: "***" },
  events: {},
  route: { tags: ["prod"] },
  quiet: null,
  flood: null,
  summary: null,
};
const CHAT = { ...OPS, name: "chat", type: "telegram", enabled: false, configured: false, settings: { bot_token: "", chat_id: "" }, route: {} };
const TYPES = [
  { name: "slack", keys: [{ key: "webhook_url", secret: true, required: true }] },
  {
    name: "telegram",
    keys: [
      { key: "bot_token", secret: true, required: true },
      { key: "chat_id", secret: false, required: true },
    ],
  },
];

function tab(extra: Record<string, RouteHandler> = {}, session?: Parameters<typeof teamsRoutes>[1], items = [OPS, CHAT]) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...teamsRoutes({}, session),
    "GET /api/teams/acme/members": () => json(200, { team: "acme", members: [] }),
    "GET /api/teams/acme/notifications/channels": () => json(200, { team: "acme", items, types: TYPES }),
    ...extra,
  });
  const harness = renderConsole("/teams/acme/notifications");
  return { ...harness, backend };
}

async function listed() {
  return screen.findByRole("list", { name: "Channels of the team" });
}

describe("the notifications of a team", () => {
  it("is a tab of the team, and lists its channels with whether each sends", async () => {
    const { container } = tab();
    expect(await screen.findByRole("link", { name: "Notifications" })).toHaveAttribute("aria-current", "page");
    const list = await listed();
    const ops = within(list).getByText("ops").closest("li");
    const chat = within(list).getByText("chat").closest("li");
    if (ops === null || chat === null) throw new Error("no rows");
    expect(within(ops).getByText("On")).toBeInTheDocument();
    expect(within(chat).getByText("Off")).toBeInTheDocument();
    expect(within(chat).getByRole("button", { name: "Set up chat" })).toBeInTheDocument();
    expect(within(ops).getByRole("button", { name: "Edit ops" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("adds a channel with its type's settings, and sends nothing about the applications it hears when none are named", async () => {
    const { backend, user } = tab({
      "PUT /api/teams/acme/notifications/channels/alerts": () => json(200, { ...OPS, name: "alerts" }),
    });
    await user.click(await screen.findByRole("button", { name: "Add a channel" }));
    const drawer = await screen.findByRole("dialog", { name: "Add a channel to acme" });
    const save = within(drawer).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await user.type(within(drawer).getByLabelText(/^Name/), "Bad Name");
    expect(await within(drawer).findByText(/Start with a lower case letter/)).toBeInTheDocument();
    expect(save).toBeDisabled();
    await user.clear(within(drawer).getByLabelText(/^Name/));
    await user.type(within(drawer).getByLabelText(/^Name/), "alerts");
    await user.type(within(drawer).getByLabelText(/^Incoming webhook URL/), "https://hooks.slack.com/services/T0/B0/x");
    await user.click(save);

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/teams/acme/notifications/channels/alerts")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/teams/acme/notifications/channels/alerts")[0]?.body).toEqual({
      type: "slack",
      enabled: true,
      settings: { webhook_url: "https://hooks.slack.com/services/T0/B0/x" },
    });
    expect(await screen.findByText("Channel alerts added")).toBeInTheDocument();
  });

  it("changes only what was changed: a stored credential is neither shown nor blanked", async () => {
    const { backend, user } = tab({
      "PUT /api/teams/acme/notifications/channels/ops": () => json(200, { ...OPS, route: { tags: ["prod", "eu"] } }),
    });
    await user.click(await screen.findByRole("button", { name: "Edit ops" }));
    const drawer = await screen.findByRole("dialog", { name: "Channel ops" });
    const url = within(drawer).getByLabelText(/^Incoming webhook URL/);
    expect(url).toHaveValue("");
    expect(url).toHaveAttribute("placeholder", "Saved - leave empty to keep it");
    const tags = within(drawer).getByLabelText(/^Tags/);
    expect(tags).toHaveValue("prod");
    await user.clear(tags);
    await user.type(tags, "prod, eu");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/teams/acme/notifications/channels/ops")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/teams/acme/notifications/channels/ops")[0]?.body).toEqual({ settings: {}, route: { tags: ["prod", "eu"] } });
  });

  it("replaces a credential only when a new one is typed", async () => {
    const { backend, user } = tab({ "PUT /api/teams/acme/notifications/channels/ops": () => json(200, OPS) });
    await user.click(await screen.findByRole("button", { name: "Edit ops" }));
    const drawer = await screen.findByRole("dialog", { name: "Channel ops" });
    await user.type(within(drawer).getByLabelText(/^Incoming webhook URL/), "https://hooks.slack.com/services/T1/B1/y");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/teams/acme/notifications/channels/ops")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/teams/acme/notifications/channels/ops")[0]?.body).toEqual({ settings: { webhook_url: "https://hooks.slack.com/services/T1/B1/y" } });
  });

  it("shows the server's refusal as it wrote it, and keeps the drawer for another try", async () => {
    const { user } = tab({
      "PUT /api/teams/acme/notifications/channels/ops": () => problem(400, "validation_error", "A team has at most 8 channels", { hint: "Remove one first." }),
    });
    await user.click(await screen.findByRole("button", { name: "Edit ops" }));
    const drawer = await screen.findByRole("dialog", { name: "Channel ops" });
    await user.click(within(drawer).getByRole("checkbox", { name: "Send to this channel" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(await within(drawer).findByText("A team has at most 8 channels")).toBeInTheDocument();
    expect(within(drawer).getByText("Remove one first.")).toBeInTheDocument();
  });

  it("sends a test and says what the receiving side answered, verbatim", async () => {
    const { backend, user } = tab({
      "POST /api/teams/acme/notifications/channels/ops/test": () => json(200, { ok: false, detail: "HTTP 404: no_service" }),
    });
    await user.click(await screen.findByRole("button", { name: "Send a test to ops" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/teams/acme/notifications/channels/ops/test")).toHaveLength(1);
    });
    expect(await screen.findByText("HTTP 404: no_service")).toBeInTheDocument();
  });

  it("removes a channel after asking", async () => {
    const { backend, user } = tab({ "DELETE /api/teams/acme/notifications/channels/ops": () => json(204, undefined) });
    await user.click(await screen.findByRole("button", { name: "Edit ops" }));
    const drawer = await screen.findByRole("dialog", { name: "Channel ops" });
    await user.click(within(drawer).getByRole("button", { name: "Remove channel" }));
    const question = await screen.findByRole("alertdialog", { name: "Remove ops?" });
    expect(backend.callsTo("DELETE /api/teams/acme/notifications/channels/ops")).toHaveLength(0);
    await user.click(within(question).getByRole("button", { name: "Remove channel" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/teams/acme/notifications/channels/ops")).toHaveLength(1);
    });
  });

  it("is read-only for a member who may not manage the team's applications", async () => {
    tab({}, viewerSession());
    const list = await listed();
    expect(within(list).getByText("ops")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add a channel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit ops" })).not.toBeInTheDocument();
    expect(screen.getByText(/Changing them takes the admin role in the team/)).toBeInTheDocument();
  });

  it("says so when the team has no channel yet, and speaks Spanish", async () => {
    tab({}, undefined, []);
    expect(await screen.findByText(/This team has no channel of its own yet/)).toBeInTheDocument();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Añadir un canal" })).toBeInTheDocument();
  });
});
