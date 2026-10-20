import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";

const DOMAIN = "shop.example.com";
const ENV_PATH = `/api/apps/${DOMAIN}/env`;

const PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nZGVm==\n-----END PRIVATE KEY-----";

const VALUES = { NODE_ENV: "production", QUEUE_URL: "redis://localhost", TLS_KEY: PEM };
const COMMENTS = { QUEUE_URL: "Where the queue lives.\nChange it with care." };
const SECRETS = {
  NODE_ENV: { secret: false, reason: "plain", marked: false },
  QUEUE_URL: { secret: false, reason: "plain", marked: false },
  TLS_KEY: { secret: false, reason: "plain", marked: false },
};

async function environmentTab(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET ${ENV_PATH}`]: (call: RecordedCall) => json(200, { domain: DOMAIN, variables: VALUES, unmasked: call.search.get("unmask") === "true", secrets: SECRETS, comments: COMMENTS }),
    [`PATCH ${ENV_PATH}`]: () => json(200, { domain: DOMAIN, changed: ["X"], restart_required: true, dry_run: false }),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/environment`);
  await waitFor(() => {
    expect(screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` })).not.toHaveAttribute("aria-busy");
  });
  return { ...harness, backend };
}

function patchBody(backend: { callsTo: (route: string) => RecordedCall[] }): unknown {
  return backend.callsTo(`PATCH ${ENV_PATH}`)[0]?.body;
}

/** The bar's split Save: "Save only" writes the change and leaves the restart for later. */
async function saveOnly(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  await user.click(screen.getByRole("button", { name: "More options for Save and restart" }));
  await user.click(await screen.findByRole("menuitem", { name: "Save only" }));
}

describe("a variable of several lines", () => {
  it("is its first line in the table, and says how many lines it has", async () => {
    await environmentTab();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });

    expect(within(table).getByText("-----BEGIN PRIVATE KEY-----")).toBeInTheDocument();
    expect(within(table).getByText("4 lines")).toBeInTheDocument();
    expect(within(table).queryByText(/MIIEvQIBADANBgkq/)).not.toBeInTheDocument();
  });

  it("is typed with line breaks in the dialog and saved whole", async () => {
    const { user, backend } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a variable" });
    await user.type(within(dialog).getByLabelText("Name"), "JSON_DOC");
    await user.click(within(dialog).getByLabelText("Value"));
    await user.paste('{\n  "a": 1\n}');
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));

    await saveOnly(user);

    await waitFor(() => {
      // Only the variable that changed travels: the file's other values are not asked for or sent.
      expect(patchBody(backend)).toEqual({ set: { JSON_DOC: '{\n  "a": 1\n}' }, unset: [], dry_run: false });
    });
  });

  it("is refused in the dialog when it holds a single quote beside the line breaks", async () => {
    const { user } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a variable" });
    await user.type(within(dialog).getByLabelText("Name"), "BAD");
    await user.click(within(dialog).getByLabelText("Value"));
    await user.paste("it's\nbroken");
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));

    expect(await within(dialog).findByText(/written between single quotes, so it cannot hold one/)).toBeInTheDocument();
    expect(screen.queryByText("1 unsaved change")).not.toBeInTheDocument();
  });

  it("is staged from a pasted file, with the lines it spans", async () => {
    const { user } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Paste .env" }));
    const paste = await screen.findByRole("dialog", { name: "Paste a .env file" });
    await user.click(within(paste).getByLabelText(".env contents"));
    await user.paste(`CERT='-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----'\nNEXT=1\n`);

    expect(within(paste).getByText("2 variables found")).toBeInTheDocument();
    expect(within(paste).getByText("-----BEGIN CERTIFICATE-----")).toBeInTheDocument();
    expect(within(paste).getByText("3 lines")).toBeInTheDocument();
  });
});

describe("the comment above a variable", () => {
  it("is shown under its name", async () => {
    await environmentTab();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });

    expect(within(table).getByText("Where the queue lives.")).toBeInTheDocument();
  });

  it("is edited in the dialog and saved alone, as the one change it is", async () => {
    const { user, backend } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Actions for NODE_ENV" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit NODE_ENV" });
    await user.type(within(dialog).getByLabelText(/^Comment/), "Always production");
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));

    expect(await screen.findByText("1 unsaved change")).toBeInTheDocument();
    // The note is under the name already: one change is saved from the bar, not reviewed first.
    expect(screen.getByText("Always production")).toBeInTheDocument();
    await saveOnly(user);

    await waitFor(() => {
      expect(patchBody(backend)).toEqual({ set: {}, unset: [], dry_run: false, comments: { NODE_ENV: "Always production" } });
    });
  });

  it("is taken away by emptying it", async () => {
    const { user, backend } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Actions for QUEUE_URL" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit QUEUE_URL" });
    const comment = within(dialog).getByLabelText(/^Comment/);
    expect(comment).toHaveValue("Where the queue lives.\nChange it with care.");
    await user.clear(comment);
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));
    expect(await screen.findByText("1 unsaved change")).toBeInTheDocument();
    await saveOnly(user);

    await waitFor(() => {
      expect(patchBody(backend)).toEqual({ set: {}, unset: [], dry_run: false, comments: { QUEUE_URL: "" } });
    });
  });

  it("is not sent when nothing about comments changed", async () => {
    const { user, backend } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Actions for NODE_ENV" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    await saveOnly(user);

    await waitFor(() => {
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)).toHaveLength(1);
    });
    expect(patchBody(backend)).toEqual({ set: {}, unset: ["NODE_ENV"], dry_run: false });
  });

  it("is plain short text, checked before it is staged", async () => {
    const { user } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Actions for NODE_ENV" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit NODE_ENV" });
    await user.click(within(dialog).getByLabelText(/^Comment/));
    await user.paste("x".repeat(501));
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));

    expect(await within(dialog).findByText("Keep the comment to 500 characters and 8 lines.")).toBeInTheDocument();
  });
});

describe("in both languages and without accessibility violations", () => {
  it("has none, and speaks Spanish", async () => {
    await environmentTab();
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByText("4 líneas")).toBeInTheDocument();
  });
});
