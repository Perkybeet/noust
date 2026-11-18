/**
 * What changes Docker through the console: looking for new versions (the one thing that asks a registry, and only when a person asks),
 * updating an image said before it is done (a new major version is a decision in words, and a database's also needs a copy proven to
 * restore), bringing a stack to Noust without touching what runs, and the Compose of a stack (read where it may be, judged before it is
 * applied, and applied with a reason).
 */

import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { onDesktop, serverRoutes } from "../server/testing";
import { canGoAhead } from "./UpdateImageDialog";
import { NOBODY, SHOP, dockerRoutes, dockerSession, image, stack } from "./testing";

beforeEach(onDesktop);

function open(path: string, more: Parameters<typeof dockerRoutes>[0] = {}) {
  const backend = fakeBackend({ ...signedInRoutes(dockerSession(true)), ...serverRoutes(), ...dockerRoutes(more) });
  return { ...renderConsole(path), backend };
}

const QUEUED = { job_id: "job-1", job: { id: "job-1", type: "docker_check", status: "queued", subject: "images", created_at: new Date().toISOString() } };

/** What the server draws for the update of an image that nobody of Noust made and that is brought first, or an application's. */
function plan(over: Record<string, unknown> = {}) {
  return {
    image_id: "sha256:aaaa",
    reference: "nginx:1.26",
    state: "newer",
    mode: "with_net",
    apply: true,
    via: "",
    needs: null,
    major: false,
    app: null,
    stack: null,
    current_version: "1.26.1",
    latest_version: "1.27.0",
    checked_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    images: ["nginx:1.26"],
    steps: ["pull", "recreate_behind_health", "keep_previous", "roll_back_on_failure"],
    backup: null,
    ...over,
  };
}

const NEWER = (state: Record<string, unknown> = {}) => ({ state: "newer", change: "minor", mode: "with_net", apply: true, reference: "nginx:1.26", owner: NOBODY, ...state }) as never;

describe("what an update may go ahead with", () => {
  it("goes ahead only when the server says it may, a new major version has been accepted and a database has a copy proven to restore", () => {
    expect(canGoAhead({ apply: true, needs: null }, false)).toBe(true);
    expect(canGoAhead({ apply: false, needs: null }, true)).toBe(false);
    expect(canGoAhead({ apply: true, needs: "accept_major" }, false)).toBe(false);
    expect(canGoAhead({ apply: true, needs: "accept_major" }, true)).toBe(true);
    // A copy is not something a person says: until there is one proven, nothing in the dialog lets it through.
    expect(canGoAhead({ apply: true, needs: "verified_copy" }, true)).toBe(false);
  });
});

describe("looking for new versions", () => {
  it("asks the registries only when a person asks, without downloading, and downloads to compare only from the menu", async () => {
    const { user, backend } = open("/server/docker/images", { images: [image("sha256:aaaa", ["nginx:1.26"], NOBODY)], "POST /api/docker/images/check": () => json(202, QUEUED) });
    await screen.findByText("nginx:1.26");
    // Nothing asked a registry by itself.
    expect(backend.callsTo("POST /api/docker/images/check")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Look for new versions" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/docker/images/check")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/docker/images/check")[0]?.body).toEqual({ pull: false });
  });

  it("offers the look by downloading in the menu, with what it costs", async () => {
    const { user, backend } = open("/server/docker/images", { images: [image("sha256:aaaa", ["nginx:1.26"], NOBODY)], "POST /api/docker/images/check": () => json(202, QUEUED) });
    await screen.findByText("nginx:1.26");
    await user.click(screen.getByRole("button", { name: "More options for Look for new versions" }));
    const item = await screen.findByRole("menuitem", { name: /Look by downloading what moved/ });
    expect(item).toHaveTextContent("It takes longer and uses the network");
    await user.click(item);
    await waitFor(() => {
      expect(backend.callsTo("POST /api/docker/images/check")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/docker/images/check")[0]?.body).toEqual({ pull: true });
  });
});

describe("updating an image", () => {
  const images = [image("sha256:aaaa", ["nginx:1.26"], NOBODY, { update: NEWER() })];

  async function openDialog(update: Record<string, unknown>) {
    const harness = open("/server/docker/images", { images, "GET /api/docker/images/sha256%3Aaaaa/update": () => json(200, update), "POST /api/docker/images/sha256%3Aaaaa/update": () => json(202, QUEUED) });
    await harness.user.click(await screen.findByRole("button", { name: "Actions for nginx:1.26" }));
    await harness.user.click(await screen.findByRole("menuitem", { name: /Update…/ }));
    const dialog = await screen.findByRole("alertdialog", { name: "Update nginx:1.26" });
    return { ...harness, dialog };
  }

  it("says what the update does, in order, before anything is done", async () => {
    const { dialog } = await openDialog(plan());
    const steps = await within(dialog).findByRole("list", { name: "What the update does, in order" });
    expect(within(steps).getAllByRole("listitem").map((step) => step.textContent)).toEqual([
      "Pull the new images",
      "Recreate the containers, one healthcheck at a time",
      "Keep the previous images, to go back to",
      "Put the previous version back if the new one does not come up",
    ]);
    expect(within(dialog).getByText("1.27.0")).toBeInTheDocument();
  });

  it("queues the update when it is confirmed, with the new major version not accepted", async () => {
    const { user, dialog, backend } = await openDialog(plan());
    const action = within(dialog).getByRole("button", { name: "Update" });
    await waitFor(() => {
      expect(action).toBeEnabled();
    });
    await user.click(action);
    await waitFor(() => {
      expect(backend.callsTo("POST /api/docker/images/sha256%3Aaaaa/update")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/docker/images/sha256%3Aaaaa/update")[0]?.body).toEqual({ accept_major: false });
  });

  it("asks for a new major version in words and does not go ahead until it is said", async () => {
    const { user, dialog, backend } = await openDialog(plan({ major: true, needs: "accept_major", current_version: "16.4", latest_version: "17.2" }));
    const action = within(dialog).getByRole("button", { name: "Update" });
    const accept = await within(dialog).findByRole("checkbox", { name: /I accept the new major version/ });
    expect(action).toBeDisabled();
    await user.click(accept);
    expect(action).toBeEnabled();
    await user.click(action);
    await waitFor(() => {
      expect(backend.callsTo("POST /api/docker/images/sha256%3Aaaaa/update")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/docker/images/sha256%3Aaaaa/update")[0]?.body).toEqual({ accept_major: true });
  });

  it("offers nothing to a database that has no copy proven to restore, and says which command makes one", async () => {
    const { dialog } = await openDialog(plan({ major: true, needs: "verified_copy", backup: { via: "noust database backup postgresql shop" } }));
    expect(await within(dialog).findByText("A database moves and has no copy proven to restore")).toBeInTheDocument();
    expect(within(dialog).getByText("noust database backup postgresql shop")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Update" })).toBeDisabled();
  });

  it("goes through the application when the image is an application's", async () => {
    const { dialog } = await openDialog(plan({ app: "shop.example.com" }));
    expect(await within(dialog).findByText(/It is updated through the application shop\.example\.com: with its healthcheck/)).toBeInTheDocument();
  });

  it("does not offer the update of what the server says is updated elsewhere, and offers to bring its stack when that is what stands in the way", async () => {
    const elsewhere = [image("sha256:bbbb", ["adminer:4"], NOBODY, { update: NEWER({ mode: "bring_first", apply: false, stack: "adminer-stack", reference: "adminer:4" }) })];
    const { user } = open("/server/docker/images", { images: elsewhere });
    await user.click(await screen.findByRole("button", { name: "Actions for adminer:4" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).queryByRole("menuitem", { name: /Update…/ })).not.toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /Bring to Noust…/ })).toHaveTextContent("Without touching what runs");
  });
});

describe("bringing a stack to Noust", () => {
  const PREVIEW = {
    stack: "nextcloud",
    name: "nextcloud",
    folder: "/srv/nextcloud",
    compose_file: "/srv/nextcloud/compose.yaml",
    counts: { containers: 4, jobs: 1, volumes: 2, networks: 1 },
    restart_loss: [{ container: "nextcloud-setup-1" }],
    warnings: [],
    preview: true,
  };

  async function openDialog() {
    const harness = open("/server/docker/stacks", {
      stacks: [stack("nextcloud", NOBODY, { total: 4, running: 3 }), stack("shop-example-com", SHOP)],
      "POST /api/docker/stacks/nextcloud/bring/preview": () => json(200, PREVIEW),
      "POST /api/docker/stacks/nextcloud/bring": () => json(200, { ...PREVIEW, preview: false, adopted: true }),
    });
    await harness.user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
    return { ...harness, dialog };
  }

  it("shows what Noust found before it registers anything, and what would not come back after a restart", async () => {
    const { dialog, backend } = await openDialog();
    expect(await within(dialog).findByText("/srv/nextcloud/compose.yaml")).toBeInTheDocument();
    expect(within(dialog).getByText("4 containers, 1 jobs that ended, 2 volumes, 1 networks")).toBeInTheDocument();
    expect(within(dialog).getByText("What would not come back after a restart")).toBeInTheDocument();
    expect(within(dialog).getByText("nextcloud-setup-1")).toBeInTheDocument();
    // Looking changes nothing and asks for nothing: it is the route of the preview, which takes no sudo mode, and nothing is registered.
    expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring/preview")[0]?.body).toEqual({ accept_recreate: false, accept_host_access: false });
    expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring")).toHaveLength(0);
  });

  it("does not ask who you are to look: the preview opens without sudo mode, and the identity is asked when it is registered", async () => {
    let elevated = false;
    const harness = open("/server/docker/stacks", {
      stacks: [stack("nextcloud", NOBODY, { total: 4, running: 3 })],
      "POST /api/docker/stacks/nextcloud/bring/preview": () => json(200, PREVIEW),
      "POST /api/docker/stacks/nextcloud/bring": () => (elevated ? json(200, { ...PREVIEW, preview: false, adopted: true }) : problem(403, "elevation_required", "Confirm it's you to continue.")),
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
      },
    });
    await harness.user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
    expect(await within(dialog).findByText("/srv/nextcloud/compose.yaml")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).not.toBeInTheDocument();
    expect(harness.backend.callsTo("POST /api/docker/stacks/nextcloud/bring")).toHaveLength(0);
    await harness.user.click(within(dialog).getByRole("button", { name: "Bring to Noust" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await harness.user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await harness.user.click(within(confirm).getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      expect(harness.backend.callsTo("POST /api/docker/stacks/nextcloud/bring")).toHaveLength(2);
    });
  });

  it("shows what Compose said when the preview is refused, verbatim, with the credentials the server already hid", async () => {
    const harness = open("/server/docker/stacks", {
      stacks: [stack("nextcloud", NOBODY, { total: 4, running: 3 })],
      "POST /api/docker/stacks/nextcloud/bring/preview": () =>
        problem(409, "docker_blocked", "Compose does not accept the stack.", { output: "service app: image nextcloud:29 pull access denied, token ****" }),
    });
    await harness.user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
    expect(await within(dialog).findByText(/service app: image nextcloud:29 pull access denied, token \*\*\*\*/)).toBeInTheDocument();
  });

  it("registers it under the name that was typed, never recreating a container", async () => {
    const { user, dialog, backend } = await openDialog();
    await user.type(await within(dialog).findByRole("textbox", { name: /Name among the applications/ }), "cloud");
    await user.click(within(dialog).getByRole("button", { name: "Bring to Noust" }));
    const registered = () => backend.callsTo("POST /api/docker/stacks/nextcloud/bring");
    await waitFor(() => {
      expect(registered()).toHaveLength(1);
    });
    expect(registered()[0]?.body).toEqual({ preview: false, accept_recreate: false, accept_host_access: false, name: "cloud" });
  });

  it("offers to bring only a stack nobody made", async () => {
    open("/server/docker/stacks", { stacks: [stack("shop-example-com", SHOP)] });
    await screen.findByText("shop-example-com");
    expect(screen.queryByRole("button", { name: "Bring to Noust…" })).not.toBeInTheDocument();
  });

  describe("a stack that runs with what gives this server to its containers", () => {
    const LINES = ["service app mounts the Docker socket", "service app is privileged"];
    /** The 409 the server answers: the lines of the Compose file, one by one, and the sentence it says of them. */
    const refusal = () =>
      json(409, {
        error: "adoptionrefusederror",
        detail: "Adopting nextcloud puts under Noust a stack whose containers have what is on this server",
        hint: "service app mounts the Docker socket\nservice app is privileged\n\nEach line above gives a container something of this server. Go on knowing that: noust docker stack bring nextcloud --accept-host-access",
        fields: null,
        output: null,
        host_access: LINES,
        coded: { code: "compose_guard.adopt_host_access", params: { domain: "nextcloud", lines: LINES.map((line) => `- ${line}`).join("\n"), command: "noust docker stack bring nextcloud --accept-host-access" } },
      });

    function withLines(extra: Parameters<typeof dockerRoutes>[0] = {}, permissions?: string[]) {
      const base = dockerSession(true);
      const session = permissions === undefined ? base : { ...base, permissions };
      const backend = fakeBackend({
        ...signedInRoutes(session),
        ...serverRoutes(),
        ...dockerRoutes({
          stacks: [stack("nextcloud", NOBODY, { total: 4, running: 3 })],
          "POST /api/docker/stacks/nextcloud/bring/preview": ((call) => ((call.body as { accept_host_access?: boolean }).accept_host_access === true ? json(200, { ...PREVIEW, warnings: ["Recorded as accepted by admin: the containers of nextcloud keep what they have of this server (2)."] }) : refusal())) satisfies RouteHandler,
          "POST /api/docker/stacks/nextcloud/bring": () => json(200, { ...PREVIEW, preview: false, adopted: true }),
          ...extra,
        }),
      });
      return { ...renderConsole("/server/docker/stacks"), backend };
    }

    it("lists each line as what it gives this server, with the server's sentence, and offers no way to go on until it is accepted", async () => {
      const { user, backend } = withLines();
      await user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
      expect(await within(dialog).findByText("Adopting nextcloud puts under Noust a stack whose containers have what is on this server")).toBeInTheDocument();
      const list = within(dialog).getByRole("list", { name: "What the containers of this stack have of this server" });
      expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual(LINES);
      // Not the error block with the command of the terminal: the way to go on is the box, beside what it accepts.
      expect(within(dialog).queryByText(/--accept-host-access/)).not.toBeInTheDocument();
      expect(within(dialog).getByRole("checkbox", { name: /I accept that the containers keep what these 2 lines give them of this server/ })).not.toBeChecked();
      expect(within(dialog).getByRole("button", { name: "Bring to Noust" })).toBeDisabled();
      expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring")).toHaveLength(0);
    });

    it("looks again accepting them when the box is ticked, and brings it with the acceptance", async () => {
      const { user, backend } = withLines();
      await user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
      await user.click(await within(dialog).findByRole("checkbox", { name: /I accept that the containers keep/ }));
      expect(await within(dialog).findByText("/srv/nextcloud/compose.yaml")).toBeInTheDocument();
      expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring/preview").map((call) => (call.body as { accept_host_access: boolean }).accept_host_access)).toEqual([false, true]);
      // What was listed stays on screen after it is accepted: it is what is being accepted.
      expect(within(dialog).getByRole("list", { name: "What the containers of this stack have of this server" })).toBeInTheDocument();
      expect(within(dialog).getByText(/Recorded as accepted by admin/)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Bring to Noust" }));
      await waitFor(() => {
        expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring")).toHaveLength(1);
      });
      expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring")[0]?.body).toEqual({ preview: false, accept_recreate: false, accept_host_access: true });
    });

    it("says who can accept them, with nothing to tick, to an account that does not hold the permission", async () => {
      const { user, backend } = withLines({}, ["self", "apps.read", "apps.manage", "docker.read", "docker.manage"]);
      await user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
      expect(await within(dialog).findByText(/Only an account that holds the root-equivalent permission can accept this/)).toBeInTheDocument();
      expect(within(dialog).getAllByRole("listitem")).toHaveLength(2);
      expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Bring to Noust" })).toBeDisabled();
      expect(backend.callsTo("POST /api/docker/stacks/nextcloud/bring")).toHaveLength(0);
    });

    it("does not touch a stack that runs with nothing of the kind: no box, and the plan as it always was", async () => {
      const { user } = withLines({ "POST /api/docker/stacks/nextcloud/bring/preview": () => json(200, PREVIEW) });
      await user.click(await screen.findByRole("button", { name: "Bring to Noust…" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Bring nextcloud to Noust" });
      expect(await within(dialog).findByText("/srv/nextcloud/compose.yaml")).toBeInTheDocument();
      expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Bring to Noust" })).toBeEnabled();
    });
  });
});

describe("the Compose of a stack", () => {
  const DOCUMENT = "services:\n  web:\n    image: nginx:1.26\n";

  const readable = (more: Record<string, unknown> = {}) => ({ stack: "shop-example-com", domain: "shop.example.com", editable: true, kind: "compose", why_code: null, why: "", text: DOCUMENT, path: "/var/www/apps/shop-example-com/current/compose.yaml", version: 3, ...more });

  it("says why a document is not edited here, in the words of the reason, and shows no editor", async () => {
    open("/server/docker/compose/nextcloud", {
      "GET /api/docker/stacks/nextcloud/compose": () => json(200, readable({ editable: false, text: null, why_code: "bring_first", why: "raw", domain: null })),
    });
    expect(await screen.findByText("This document is edited elsewhere")).toBeInTheDocument();
    expect(screen.getByText("Noust did not create this stack. Bring it to Noust first, without touching it, and it can be edited here.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Compose document of nextcloud/ })).not.toBeInTheDocument();
  });

  it("does not read the document of whoever may not reveal secrets, and says what it takes", async () => {
    const session = { ...dockerSession(true), permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "secrets.reveal") };
    const backend = fakeBackend({ ...signedInRoutes(session), ...serverRoutes(), ...dockerRoutes() });
    renderConsole("/server/docker/compose/shop-example-com");
    expect(await screen.findByText("Editing a Compose takes two permissions")).toBeInTheDocument();
    expect(backend.callsTo("GET /api/docker/stacks/shop-example-com/compose")).toHaveLength(0);
  });

  it("is judged before it is applied, and applied with a reason that is typed", async () => {
    const verdict = { valid: true, changed: true, problems: [], output: "", diff: "", effects: { removes_volumes: false }, checks: { compose: true, privileges: true, images: true, variables: true } };
    const { user, backend } = open("/server/docker/compose/shop-example-com", {
      "GET /api/docker/stacks/shop-example-com/compose": () => json(200, readable()),
      "GET /api/docker/stacks/shop-example-com/compose/versions": () => json(200, { items: [] }),
      "POST /api/docker/stacks/shop-example-com/compose/check": () => json(200, verdict),
      "PUT /api/docker/stacks/shop-example-com/compose": () => json(202, QUEUED),
    });
    const editor = await screen.findByRole("textbox", { name: "Compose document of shop-example-com" });
    // Looking at it judged nothing and applied nothing.
    expect(backend.callsTo("POST /api/docker/stacks/shop-example-com/compose/check")).toHaveLength(0);
    await user.type(editor, "# more memory\n");
    await user.click(screen.getByRole("button", { name: "Test and save" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Apply the Compose of shop-example-com" });
    expect(backend.callsTo("POST /api/docker/stacks/shop-example-com/compose/check")).toHaveLength(1);
    expect(backend.callsTo("PUT /api/docker/stacks/shop-example-com/compose")).toHaveLength(0);
    const apply = within(dialog).getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox", { name: /Why/ }), "More memory for the worker");
    expect(apply).toBeEnabled();
    await user.click(apply);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/docker/stacks/shop-example-com/compose")).toHaveLength(1);
    });
    const sent = backend.callsTo("PUT /api/docker/stacks/shop-example-com/compose")[0]?.body as { text: string; reason: string };
    expect(sent.reason).toBe("More memory for the worker");
    expect(sent.text).toContain("# more memory");
    expect(sent.text).toContain("image: nginx:1.26");
  });
});

describe("the cleanup", () => {
  const PLAN = {
    available: true,
    engine: null,
    token: "plan-1",
    kept: [{ kind: "image", name: "shop-example-com-web:release-40", why: "rollback", size_bytes: 393_000_000 }],
    default_bytes: 362_000_000,
    items: [
      { id: "sha256:6666", kind: "image", name: "<none> 666666666666", owner: NOBODY, size_bytes: 362_000_000, why: "dangling", tick: true, type_name: false, anonymous: false },
      { id: "wiki_data", kind: "volume", name: "wiki_data", owner: NOBODY, size_bytes: 3_650_722_201, why: "unused", tick: false, type_name: true, anonymous: false },
    ],
  };

  async function openDialog() {
    const harness = open("/server/docker", { "GET /api/docker/cleanup/plan": () => json(200, PLAN), "POST /api/docker/cleanup": () => json(202, QUEUED) });
    await harness.user.click(await screen.findByRole("button", { name: "Clean up…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Clean up Docker" });
    return { ...harness, dialog };
  }

  it("is a plan: what would go, whose it is, what a volume holds, and what an application keeps to go back to stays", async () => {
    const { dialog } = await openDialog();
    expect(await within(dialog).findByRole("checkbox", { name: /<none> 666666666666/ })).toBeChecked();
    // A volume holds data: it is offered, and never ticked by itself.
    expect(within(dialog).getByRole("checkbox", { name: /wiki_data/ })).not.toBeChecked();
    expect(within(dialog).getByRole("button", { name: /^Remove what is ticked \(/ })).toBeInTheDocument();
    expect(within(dialog).getByText("1 thing looks unused and stays")).toBeInTheDocument();
  });

  it("asks for the name of a volume that is ticked, and sends what was typed with what was chosen", async () => {
    const { user, dialog, backend } = await openDialog();
    await user.click(await within(dialog).findByRole("checkbox", { name: /wiki_data/ }));
    const action = within(dialog).getByRole("button", { name: /^Remove what is ticked/ });
    expect(action).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox", { name: "Type wiki_data to delete it" }), "wiki_data");
    expect(action).toBeEnabled();
    await user.click(action);
    await waitFor(() => {
      expect(backend.callsTo("POST /api/docker/cleanup")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/docker/cleanup")[0]?.body).toEqual({ token: "plan-1", select: ["sha256:6666", "wiki_data"], confirm_names: { wiki_data: "wiki_data" } });
  });
});
