import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";

const DOMAIN = "shop.example.com";
const ENV_PATH = `/api/apps/${DOMAIN}/env`;
const MARKS_PATH = `${ENV_PATH}/marks`;

const MASKED = {
  NODE_ENV: "production",
  DATABASE_URL: "postgres://shop:***@127.0.0.1:5432/shop",
  SESSION_SECRET: "***",
};

const CLEAR = {
  NODE_ENV: "production",
  DATABASE_URL: "postgres://shop:hunter2@127.0.0.1:5432/shop",
  SESSION_SECRET: "s3cr3t-value",
};

interface Secrecy {
  secret: boolean;
  reason: string;
  marked: boolean;
}

const SECRETS: Record<string, Secrecy> = {
  NODE_ENV: { secret: false, reason: "plain", marked: false },
  DATABASE_URL: { secret: true, reason: "url credentials", marked: false },
  SESSION_SECRET: { secret: true, reason: "name", marked: false },
};

function envRoute(
  masked: Record<string, string> = MASKED,
  clear: Record<string, string> = CLEAR,
  secrets: Record<string, Secrecy> = SECRETS,
): RouteHandler {
  return (call: RecordedCall) => {
    const unmasked = call.search.get("unmask") === "true";
    return json(200, { domain: DOMAIN, variables: unmasked ? clear : masked, unmasked, secrets });
  };
}

async function environmentTab(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET ${ENV_PATH}`]: envRoute(),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/environment`);
  // The placeholder is the same table with placeholder rows; the loaded one is not busy.
  await waitFor(() => {
    expect(screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` })).not.toHaveAttribute("aria-busy");
  });
  return { ...harness, backend };
}

/** Opens a variable's row menu, "Actions for NAME", and chooses one of its items. */
async function rowAction(user: ReturnType<typeof renderConsole>["user"], name: string, item: string): Promise<void> {
  await user.click(screen.getByRole("button", { name: `Actions for ${name}` }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
}

const SAVED = { domain: DOMAIN, changed: ["NODE_ENV"], restart_required: true, dry_run: false };
const RESTARTED = { success: true, message: "Application restarted", domain: DOMAIN };

/** The bar's split Save: "Save only" writes the change and leaves the restart for later. */
async function saveOnly(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  await user.click(screen.getByRole("button", { name: "More options for Save and restart" }));
  await user.click(await screen.findByRole("menuitem", { name: "Save only" }));
}

function unmaskCalls(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.method === "GET" && call.path === ENV_PATH && call.search.get("unmask") === "true");
}

describe("the environment tab", () => {
  it("shows plain values, masks secrets until revealed, and reads them in clear only when asked", async () => {
    const { user, backend } = await environmentTab();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
    // A plain value is in view: it is not a secret, so hiding it would only say it is one.
    expect(within(table).getByText("production")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reveal the value of NODE_ENV" })).not.toBeInTheDocument();
    expect(within(table).getAllByText("Hidden")).toHaveLength(2);
    expect(unmaskCalls(backend.calls)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Reveal the value of SESSION_SECRET" }));
    expect(await within(table).findByText("s3cr3t-value")).toBeInTheDocument();
    expect(unmaskCalls(backend.calls)).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Hide the value of SESSION_SECRET" }));
    expect(within(table).queryByText("s3cr3t-value")).not.toBeInTheDocument();
  });

  it("asks to confirm it's you before a secret is shown, then shows it", async () => {
    let elevated = false;
    const { user, backend } = await environmentTab({
      [`GET ${ENV_PATH}`]: (call) =>
        call.search.get("unmask") === "true" && !elevated
          ? problem(403, "elevation_required", "Confirm it's you to continue")
          : envRoute()(call),
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: "2026-09-25T20:10:00+00:00" });
      },
    });
    await user.click(screen.getByRole("button", { name: "Reveal the value of SESSION_SECRET" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("s3cr3t-value")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/elevate")).toHaveLength(1);
  });

  it("explains why each variable is hidden or shown, and lets an operator override it immediately", async () => {
    let marked = false;
    const { user, backend } = await environmentTab({
      [`GET ${ENV_PATH}`]: (call) =>
        envRoute(
          MASKED,
          CLEAR,
          marked ? { ...SECRETS, SESSION_SECRET: { secret: false, reason: "marked not secret", marked: true } } : SECRETS,
        )(call),
      [`PUT ${MARKS_PATH}`]: () => {
        marked = true;
        return json(200, {
          domain: DOMAIN,
          secrets: { ...SECRETS, SESSION_SECRET: { secret: false, reason: "marked not secret", marked: true } },
        });
      },
    });

    expect(screen.getByText(/Values that look like secrets are hidden/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
    const typeOf = (name: string): HTMLElement => {
      const row = within(table).getByText(name, { exact: true }).closest("tr");
      if (!row) throw new Error(`no row for ${name}`);
      return within(row).getAllByRole("cell")[2] ?? row;
    };
    expect(typeOf("NODE_ENV")).toHaveTextContent(/^Plain$/);
    expect(typeOf("DATABASE_URL")).toHaveTextContent("Secret (the URL has a password)");
    expect(typeOf("SESSION_SECRET")).toHaveTextContent("Secret (by its name)");
    // The whole reason is the cell's title.
    expect(typeOf("SESSION_SECRET").querySelector("[title]")).toHaveAttribute("title", "Hidden: its name suggests a secret");

    await user.click(screen.getByRole("button", { name: "Actions for SESSION_SECRET" }));
    // The current choice is named, and cannot be chosen again.
    expect(await screen.findByRole("menuitem", { name: "Decide automatically (current)" })).toHaveAttribute("aria-disabled", "true");
    await user.click(screen.getByRole("menuitem", { name: "No, always show it" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${MARKS_PATH}`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT ${MARKS_PATH}`)[0]?.body).toEqual({ marks: { SESSION_SECRET: false } });
    await waitFor(() => {
      expect(typeOf("SESSION_SECRET")).toHaveTextContent("Plain (marked by you)");
    });
  });

  it("asks to confirm it's you before changing a mark, then applies it", async () => {
    let elevated = false;
    let marked = false;
    const { user } = await environmentTab({
      [`GET ${ENV_PATH}`]: (call) =>
        envRoute(
          MASKED,
          CLEAR,
          marked ? { ...SECRETS, NODE_ENV: { secret: true, reason: "marked secret", marked: true } } : SECRETS,
        )(call),
      [`PUT ${MARKS_PATH}`]: () => {
        if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue");
        marked = true;
        return json(200, { domain: DOMAIN, secrets: { ...SECRETS, NODE_ENV: { secret: true, reason: "marked secret", marked: true } } });
      },
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: "2026-09-25T20:10:00+00:00" });
      },
    });

    await rowAction(user, "NODE_ENV", "Yes, always hide it");
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByTitle("Hidden: marked secret by you")).toHaveTextContent("Secret (marked by you)");
  });

  it("stages a pasted file, reviews it without reading a secret, and writes only what changes", async () => {
    const { user, backend } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => json(200, SAVED),
    });
    await user.click(screen.getByRole("button", { name: "Paste .env" }));
    const paste = await screen.findByRole("dialog", { name: "Paste a .env file" });
    await user.click(within(paste).getByLabelText(".env contents"));
    await user.paste('# comment\r\nNODE_ENV=staging\r\nPORT = "3000"\r\n\r\nnot an assignment\r\nPORT=8080\r\n');
    expect(within(paste).getByText("2 variables found")).toBeInTheDocument();
    expect(within(paste).getByText("PORT is set on lines 3 and 6; the later line wins.")).toBeInTheDocument();
    expect(within(paste).getByText("Line 5 has no = and is skipped, as Noust skips it.")).toBeInTheDocument();
    await user.click(within(paste).getByRole("radio", { name: "Replace all" }));
    expect(within(paste).getByText(/2 current variables are removed/)).toBeInTheDocument();
    await user.click(within(paste).getByRole("button", { name: "Stage 2 variables" }));

    expect(await screen.findByText("4 unsaved changes")).toBeInTheDocument();
    // A pasted file is looked over whole before it is written, however few variables it holds.
    await user.click(screen.getByRole("button", { name: "Save and restart" }));
    const review = await screen.findByRole("dialog", { name: "Review changes" });
    expect(within(review).getByText("1 added, 1 changed, 2 removed. 0 variables stay as they are.")).toBeInTheDocument();
    // The removed secrets are the placeholders the server sent: nothing was read in clear to compare.
    await user.click(within(review).getByRole("switch", { name: "Show values" }));
    expect(within(review).queryByText("s3cr3t-value")).not.toBeInTheDocument();
    expect(within(review).getByText("postgres://shop:***@127.0.0.1:5432/shop")).toBeInTheDocument();
    await user.click(within(review).getByRole("button", { name: "Save only" }));

    await waitFor(() => {
      // The `# comment` above NODE_ENV in the pasted file is the note kept above it.
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)[0]?.body).toEqual({
        set: { NODE_ENV: "staging", PORT: "8080" },
        unset: ["DATABASE_URL", "SESSION_SECRET"],
        dry_run: false,
        comments: { NODE_ENV: "comment" },
      });
    });
    expect(unmaskCalls(backend.calls)).toHaveLength(0);
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/restart`)).toHaveLength(0);
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Review changes" })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toHaveTextContent("No unsaved changes");
  });

  it("restarts the app after saving when asked to, and not when nothing changed on the server", async () => {
    let changed = ["NODE_ENV"];
    const { user, backend } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => json(200, { ...SAVED, changed }),
      [`POST /api/apps/${DOMAIN}/restart`]: () => json(200, RESTARTED),
    });
    await rowAction(user, "NODE_ENV", "Edit");
    const dialog = await screen.findByRole("dialog", { name: "Edit NODE_ENV" });
    await user.clear(within(dialog).getByLabelText("Value"));
    await user.type(within(dialog).getByLabelText("Value"), "staging");
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();

    // One change is saved from the bar: no review in between.
    await user.click(screen.getByRole("button", { name: "Save and restart" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)[0]?.body).toEqual({ set: { NODE_ENV: "staging" }, unset: [], dry_run: false });
    });
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/restart`)).toHaveLength(1);
    });

    changed = [];
    await rowAction(user, "NODE_ENV", "Edit");
    const again = await screen.findByRole("dialog", { name: "Edit NODE_ENV" });
    await user.clear(within(again).getByLabelText("Value"));
    await user.type(within(again).getByLabelText("Value"), "staging");
    await user.click(within(again).getByRole("button", { name: "Update variable" }));
    await user.click(screen.getByRole("button", { name: "Save and restart" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)).toHaveLength(2);
    });
    // The file already held it: there is nothing to restart for.
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/restart`)).toHaveLength(1);
  });

  it("adds and removes rows as a draft, and writes those two names only, reading no secret", async () => {
    const { user, backend } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => json(200, SAVED),
    });
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a variable" });
    await user.type(within(dialog).getByLabelText("Name"), "API_URL");
    await user.type(within(dialog).getByLabelText("Value"), "https://api.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));
    await rowAction(user, "NODE_ENV", "Remove");
    expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();

    await saveOnly(user);
    await waitFor(() => {
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)[0]?.body).toEqual({ set: { API_URL: "https://api.example.com" }, unset: ["NODE_ENV"], dry_run: false });
    });
    expect(unmaskCalls(backend.calls)).toHaveLength(0);
  });

  it("writes six changes straight from the bar, with no review in between", async () => {
    const many = Object.fromEntries(Array.from({ length: 8 }, (_, index) => [`VAR_${String(index)}`, "x"]));
    const secrets = Object.fromEntries(Object.keys(many).map((name) => [name, { secret: false, reason: "plain", marked: false }]));
    const { user, backend } = await environmentTab({
      [`GET ${ENV_PATH}`]: envRoute(many, many, secrets),
      [`PATCH ${ENV_PATH}`]: () => json(200, SAVED),
    });
    for (let index = 0; index < 6; index += 1) await rowAction(user, `VAR_${String(index)}`, "Remove");
    expect(screen.getByText("6 unsaved changes")).toBeInTheDocument();
    await saveOnly(user);
    await waitFor(() => {
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)).toHaveLength(1);
    });
    expect(screen.queryByRole("dialog", { name: "Review changes" })).not.toBeInTheDocument();
  });

  it("asks for a review past six changes", async () => {
    const many = Object.fromEntries(Array.from({ length: 8 }, (_, index) => [`VAR_${String(index)}`, "x"]));
    const secrets = Object.fromEntries(Object.keys(many).map((name) => [name, { secret: false, reason: "plain", marked: false }]));
    const { user, backend } = await environmentTab({
      [`GET ${ENV_PATH}`]: envRoute(many, many, secrets),
      [`PATCH ${ENV_PATH}`]: () => json(200, SAVED),
    });
    for (let index = 0; index < 7; index += 1) await rowAction(user, `VAR_${String(index)}`, "Remove");
    expect(screen.getByText("7 unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save and restart" }));
    const review = await screen.findByRole("dialog", { name: "Review changes" });
    expect(within(review).getByText("7 removed. 1 variable stays as it is.")).toBeInTheDocument();
    expect(backend.callsTo(`PATCH ${ENV_PATH}`)).toHaveLength(0);
    await user.click(within(review).getByRole("button", { name: "Cancel" }));
    // Cancelling the review keeps the draft.
    expect(screen.getByText("7 unsaved changes")).toBeInTheDocument();
  });

  it("opens a variable on pressing its row, and shows what a change will do in the row itself", async () => {
    const { user } = await environmentTab();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
    await user.click(within(table).getByText("NODE_ENV"));
    const dialog = await screen.findByRole("dialog", { name: "Edit NODE_ENV" });
    await user.clear(within(dialog).getByLabelText("Value"));
    await user.type(within(dialog).getByLabelText("Value"), "staging");
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));

    const row = within(table).getByText("NODE_ENV").closest("tr");
    if (row === null) throw new Error("no row for NODE_ENV");
    // The old value struck, the new one beside it: no dialog to open to see the change.
    expect(within(row).getByText("production")).toBeInTheDocument();
    expect(within(row).getByText("staging")).toBeInTheDocument();
    expect(within(row).getByText("Changed")).toBeInTheDocument();
  });

  it("never reads a hidden value to edit it: it is kept, or replaced, and only the replacement is sent", async () => {
    const { user, backend } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => json(200, { ...SAVED, changed: ["SESSION_SECRET"] }),
    });
    await rowAction(user, "SESSION_SECRET", "Edit");
    const dialog = await screen.findByRole("dialog", { name: "Edit SESSION_SECRET" });
    // The value is not shown, not asked for, and not editable until the operator chooses to replace it.
    expect(within(dialog).getByLabelText("Value")).toBeDisabled();
    expect(within(dialog).getByText("The value is hidden. It stays as it is unless you replace it.")).toBeInTheDocument();
    expect(unmaskCalls(backend.calls)).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Replace value" }));
    const value = within(dialog).getByLabelText("Value");
    expect(value).toBeEnabled();
    expect(value).toHaveFocus();
    await user.type(value, "a-new-secret");
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));

    // A changed secret is not drawn, old or new.
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
    expect(within(table).queryByText("a-new-secret")).not.toBeInTheDocument();
    await saveOnly(user);
    await waitFor(() => {
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)[0]?.body).toEqual({ set: { SESSION_SECRET: "a-new-secret" }, unset: [], dry_run: false });
    });
    expect(unmaskCalls(backend.calls)).toHaveLength(0);
  });

  it("keeps a hidden value as it is when only its audience changes", async () => {
    const { user, backend } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => json(200, { ...SAVED, changed: ["SESSION_SECRET"] }),
    });
    await rowAction(user, "SESSION_SECRET", "Edit");
    const dialog = await screen.findByRole("dialog", { name: "Edit SESSION_SECRET" });
    await user.click(within(dialog).getByRole("combobox", { name: "Given to" }));
    await user.click(await screen.findByRole("option", { name: "Build only" }));
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));

    await saveOnly(user);
    await waitFor(() => {
      // No value: the secret is not in the request at all.
      expect(backend.callsTo(`PATCH ${ENV_PATH}`)[0]?.body).toEqual({ set: {}, unset: [], dry_run: false, audience: { SESSION_SECRET: "build" } });
    });
  });

  it("shows the API's refusal verbatim and keeps the draft", async () => {
    const { user } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => problem(422, "validation_error", "Environment variable 'NOTE' contains a tab"),
    });
    await rowAction(user, "NODE_ENV", "Edit");
    const dialog = await screen.findByRole("dialog", { name: "Edit NODE_ENV" });
    const value = within(dialog).getByLabelText("Value");
    await user.clear(value);
    await user.type(value, "staging");
    await user.click(within(dialog).getByRole("button", { name: "Update variable" }));
    await saveOnly(user);
    // The refusal is a toast with the system's own words; the draft is still there to fix.
    expect(await screen.findByText("The environment was not saved")).toBeInTheDocument();
    expect(await screen.findByText("Environment variable 'NOTE' contains a tab")).toBeInTheDocument();
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("shows the refusal inside the review when a pasted file is refused", async () => {
    const { user } = await environmentTab({
      [`PATCH ${ENV_PATH}`]: () => problem(422, "validation_error", "Environment variable 'NOTE' contains a tab"),
    });
    await user.click(screen.getByRole("button", { name: "Paste .env" }));
    const paste = await screen.findByRole("dialog", { name: "Paste a .env file" });
    await user.click(within(paste).getByLabelText(".env contents"));
    await user.paste("PORT=3000\n");
    await user.click(within(paste).getByRole("button", { name: "Stage 1 variable" }));
    await user.click(screen.getByRole("button", { name: "Save and restart" }));
    const review = await screen.findByRole("dialog", { name: "Review changes" });
    await user.click(within(review).getByRole("button", { name: "Save and restart" }));
    expect(await within(review).findByText("Environment variable 'NOTE' contains a tab")).toBeInTheDocument();
    expect(within(review).getByText("The environment was not saved")).toBeInTheDocument();
  });

  it("keeps the save bar in place from the first frame, and names the file it writes", async () => {
    await environmentTab();
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toHaveTextContent("No unsaved changes");
    expect(screen.getByRole("button", { name: "Save and restart" })).toBeDisabled();
    expect(screen.getByText(/^Stored in/)).toBeInTheDocument();
  });

  it("draws each variable as a card on a phone, its menu in view", async () => {
    fakeBackend({ ...signedInRoutes(), [`GET ${ENV_PATH}`]: envRoute() });
    const { user } = renderConsole(`/apps/${DOMAIN}/environment`);
    await waitFor(() => {
      expect(within(screen.getByRole("list", { name: `Environment variables of ${DOMAIN}` })).getAllByRole("listitem")).toHaveLength(3);
    });
    const list = screen.getByRole("list", { name: `Environment variables of ${DOMAIN}` });
    await user.click(within(list).getByRole("button", { name: "Actions for NODE_ENV" }));
    expect(await screen.findByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
  });

  it("invites a first variable when the file is empty", async () => {
    fakeBackend({
      ...signedInRoutes(),
      [`GET ${ENV_PATH}`]: envRoute({}, {}),
    });
    renderConsole(`/apps/${DOMAIN}/environment`);
    expect(await screen.findByRole("heading", { name: "No environment variables" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Paste .env" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add variable" })).toBeInTheDocument();
  });

  it("reads export prefixes as a shell would, and refuses a name the API refuses", async () => {
    const { user } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Paste .env" }));
    const paste = await screen.findByRole("dialog", { name: "Paste a .env file" });
    const textarea = within(paste).getByLabelText(".env contents");
    await user.click(textarea);
    await user.paste("export NODE_ENV=production\nPORT=3000\n");
    expect(within(paste).getByRole("button", { name: "Stage 2 variables" })).toBeEnabled();

    await user.clear(textarea);
    await user.paste("MY VAR=1\nPORT=3000\n");
    expect(within(paste).getByText(/Line 1: "MY VAR" is not a variable name/)).toBeInTheDocument();
    expect(within(paste).getByRole("button", { name: "Stage 2 variables" })).toBeDisabled();
  });

  // axe over the whole tab takes seconds when the suite runs every file at once.
  it("has no accessibility violations", { timeout: 20_000 }, async () => {
    const { user } = await environmentTab();
    await user.click(screen.getByRole("button", { name: "Reveal the value of SESSION_SECRET" }));
    await screen.findByText("s3cr3t-value");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it(
    "translates the table, the paste dialog and the review dialog, with no accessibility violations",
    { timeout: 20_000 },
    async () => {
      const { user, backend } = await environmentTab({
        [`PATCH ${ENV_PATH}`]: () => json(200, SAVED),
        [`POST /api/apps/${DOMAIN}/restart`]: () => json(200, RESTARTED),
      });
      await act(() => setLocale("es"));

      const table = screen.getByRole("table", { name: `Variables de entorno de ${DOMAIN}` });
      expect(within(table).getAllByText("Oculto")).toHaveLength(2);
      expect(within(table).getAllByText("Secreto")).toHaveLength(2);
      expect(screen.getByRole("button", { name: "Pegar .env" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Añadir variable" })).toBeInTheDocument();
      await expectNoAxeViolations(screen.getByRole("main"));

      await user.click(screen.getByRole("button", { name: "Pegar .env" }));
      const paste = await screen.findByRole("dialog", { name: "Pegar un archivo .env" });
      await user.click(within(paste).getByLabelText("Contenido del .env"));
      await user.paste("PORT=3000\n");
      expect(within(paste).getByText("1 variable encontrada")).toBeInTheDocument();
      await user.click(within(paste).getByRole("button", { name: "Preparar 1 variable" }));

      expect(await screen.findByText("1 cambio sin guardar")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Guardar y reiniciar" }));
      const review = await screen.findByRole("dialog", { name: "Revisar cambios" });
      await expectNoAxeViolations(review);
      await user.click(within(review).getByRole("button", { name: "Guardar y reiniciar" }));
      await waitFor(() => {
        expect(backend.callsTo(`PATCH ${ENV_PATH}`)).toHaveLength(1);
      });
      await waitFor(() => {
        expect(backend.callsTo(`POST /api/apps/${DOMAIN}/restart`)).toHaveLength(1);
      });
    },
  );
});
