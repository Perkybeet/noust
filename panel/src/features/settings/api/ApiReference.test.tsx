import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const DOCUMENT = {
  info: { title: "Noust", version: "1" },
  paths: {
    "/api/apps": {
      get: {
        summary: "List Apps",
        description: "List the applications.\n\nReturns:\n    Every application.",
        tags: ["Applications"],
        "x-noust-permission": "apps.read",
        parameters: [{ in: "query", name: "team", schema: { type: "string" }, description: "Only this team's" }],
        responses: { "200": { description: "Successful Response" } },
      },
    },
    "/api/apps/{domain}": {
      get: {
        summary: "Get App",
        tags: ["Applications"],
        "x-noust-permission": "apps.read",
        parameters: [{ in: "path", name: "domain", required: true, schema: { type: "string" } }],
        responses: { "200": { description: "Successful Response" }, "404": { description: "No such application" } },
      },
    },
    "/api/jobs/update": {
      post: {
        summary: "Create Update Job",
        tags: ["Jobs"],
        "x-noust-permission": "apps.deploy",
        "x-noust-requires-elevation": true,
        requestBody: { content: { "application/json": { schema: { $ref: "#/components/schemas/UpdateRequest" } } } },
        responses: { "202": { description: "Update job created" } },
      },
    },
  },
  components: {
    schemas: {
      UpdateRequest: {
        type: "object",
        required: ["domain"],
        properties: { domain: { type: "string", description: "Domain of the application" }, force: { type: "boolean", default: false } },
      },
    },
  },
};

async function pageAt(path = "/settings/api", extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/openapi.json": () => json(200, DOCUMENT),
    ...extra,
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return { ...harness, backend };
}

describe("Settings > API reference", () => {
  it("lists every route the server describes, with the permission each needs, and passes axe", { timeout: 30_000 }, async () => {
    await pageAt();
    await screen.findByText("/api/jobs/update");
    const table = screen.getByRole("region", { name: "API routes" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    const update = within(table).getByText("/api/jobs/update").closest("tr");
    if (!update) throw new Error("no row for the update route");
    expect(within(update).getByText("POST")).toBeInTheDocument();
    expect(within(update).getByText("apps.deploy")).toBeInTheDocument();
    expect(within(update).getByText("Sudo mode")).toBeInTheDocument();
    expect(screen.getByText("3 of 3 routes")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the settings navigation, as the server's own section", async () => {
    await pageAt();
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "API reference" })).toHaveAttribute("aria-current", "page");
  });

  it("filters by what is typed and by area, and the filter is in the address", { timeout: 30_000 }, async () => {
    const { user, location } = await pageAt();
    await screen.findByText("/api/jobs/update");
    await user.type(screen.getByRole("searchbox", { name: "Search routes" }), "deploy");
    await waitFor(() => {
      expect(screen.getByText("1 of 3 routes")).toBeInTheDocument();
    });
    expect(location().search).toMatchObject({ q: "deploy" });
    expect(screen.queryByText("/api/apps")).not.toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox", { name: "Search routes" }));
    await user.type(screen.getByRole("searchbox", { name: "Search routes" }), "zzz");
    expect(await screen.findByText("No route matches")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("3 of 3 routes")).toBeInTheDocument();
  });

  it("opens a change with what it needs, takes and answers, and a curl that never holds a token", { timeout: 30_000 }, async () => {
    const { user } = await pageAt();
    await user.click(await screen.findByText("/api/jobs/update"));
    const drawer = await screen.findByRole("dialog", { name: "POST /api/jobs/update" });
    expect(within(drawer).getByText("Create Update Job")).toBeInTheDocument();
    expect(within(drawer).getByText("apps.deploy")).toBeInTheDocument();
    expect(within(drawer).getByText("Confirm it's you (sudo mode) first")).toBeInTheDocument();
    expect(within(drawer).getByText("Domain of the application")).toBeInTheDocument();
    expect(within(drawer).getByText("Update job created")).toBeInTheDocument();
    const curl = within(drawer).getByText(/^curl -sS -X POST/);
    expect(curl.textContent).toContain('Bearer $NOUST_TOKEN');
    expect(curl.textContent).not.toContain("noust_tok_");
    // A change can be tried from the page too, with an example body to edit first.
    const body = within(drawer).getByLabelText("Body to send");
    expect(body).toHaveValue(JSON.stringify({ domain: "string", force: false }, null, 2));
    expect(within(drawer).getByRole("button", { name: "Send request" })).toBeEnabled();
  });

  it("sends a change only after asking, with the body as edited, and shows its status, headers and body", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt("/settings/api", {
      "POST /api/jobs/update": () => json(202, { job_id: "j-7", status: "queued" }, { "X-Noust-API-Version": "1" }),
    });
    await user.click(await screen.findByText("/api/jobs/update"));
    const drawer = await screen.findByRole("dialog", { name: "POST /api/jobs/update" });
    const body = within(drawer).getByLabelText("Body to send");
    await user.clear(body);
    await user.click(body);
    await user.paste('{"domain": "shop.example.com", "force": true}');

    await user.click(within(drawer).getByRole("button", { name: "Send request" }));
    // Nothing has gone yet: it is the real server.
    const confirm = await screen.findByRole("alertdialog", { name: "Send POST /api/jobs/update?" });
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(0);
    expect(within(confirm).getByText(/not a rehearsal/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Send POST" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: "shop.example.com", force: true });
    expect(await within(drawer).findByText("Status 202")).toBeInTheDocument();
    expect(within(drawer).getByText(/"job_id": "j-7"/)).toBeInTheDocument();
    await user.click(within(drawer).getByText("Response headers"));
    expect(within(drawer).getByText(/x-noust-api-version: 1/)).toBeInTheDocument();
  });

  it("sends nothing when the question is declined", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt("/settings/api", { "POST /api/jobs/update": () => json(202, {}) });
    await user.click(await screen.findByText("/api/jobs/update"));
    const drawer = await screen.findByRole("dialog", { name: "POST /api/jobs/update" });

    await user.click(within(drawer).getByRole("button", { name: "Send request" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Send POST /api/jobs/update?" });
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(0);
    expect(within(drawer).queryByText(/^Status \d/)).not.toBeInTheDocument();
  });

  it("does not send a body that is not JSON, and says so", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt();
    await user.click(await screen.findByText("/api/jobs/update"));
    const drawer = await screen.findByRole("dialog", { name: "POST /api/jobs/update" });
    const body = within(drawer).getByLabelText("Body to send");
    await user.clear(body);
    await user.click(body);
    await user.paste("{domain:");

    expect(await within(drawer).findByText("This is not valid JSON.")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Send request" })).toBeDisabled();
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(0);
  });

  it("shows a change's refusal with its status and the words the server wrote", { timeout: 30_000 }, async () => {
    const { user } = await pageAt("/settings/api", {
      "POST /api/jobs/update": () => problem(403, "forbidden", "You may not update applications.", { hint: "Ask for the operator role." }),
    });
    await user.click(await screen.findByText("/api/jobs/update"));
    const drawer = await screen.findByRole("dialog", { name: "POST /api/jobs/update" });
    await user.click(within(drawer).getByRole("button", { name: "Send request" }));
    await user.click(await screen.findByRole("button", { name: "Send POST" }));

    expect(await within(drawer).findByText("Status 403")).toBeInTheDocument();
    expect(within(drawer).getByText("You may not update applications.")).toBeInTheDocument();
    expect(within(drawer).getByText("Ask for the operator role.")).toBeInTheDocument();
  });

  it("shows a read's status too, and a read is sent without asking", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt("/settings/api", { "GET /api/apps": () => json(200, []) });
    await user.click(await screen.findByText("/api/apps"));
    const drawer = await screen.findByRole("dialog", { name: "GET /api/apps" });
    await user.click(within(drawer).getByRole("button", { name: "Send request" }));

    expect(await within(drawer).findByText("Status 200")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(backend.callsTo("GET /api/apps")).toHaveLength(1);
  });

  it("tries a read with the session and shows the answer as it came, after the parameters it needs", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt("/settings/api", {
      "GET /api/apps/shop.example.com": () => json(200, { domain: "shop.example.com", status: "running" }),
    });
    await user.click(await screen.findByText("/api/apps/{domain}"));
    const drawer = await screen.findByRole("dialog", { name: "GET /api/apps/{domain}" });
    const send = within(drawer).getByRole("button", { name: "Send request" });
    expect(send).toBeDisabled();
    await user.type(within(drawer).getByLabelText(/^domain/), "shop.example.com");
    await user.click(send);
    await waitFor(() => {
      expect(backend.callsTo("GET /api/apps/shop.example.com")).toHaveLength(1);
    });
    expect(await within(drawer).findByText("Status 200")).toBeInTheDocument();
    expect(within(drawer).getByText(/"status": "running"/)).toBeInTheDocument();
  });

  it("shows a read's refusal as the server wrote it", { timeout: 30_000 }, async () => {
    const { user } = await pageAt("/settings/api", { "GET /api/apps": () => problem(403, "forbidden", "You may not list applications.", { hint: "Ask for the viewer role." }) });
    await user.click(await screen.findByText("/api/apps"));
    const drawer = await screen.findByRole("dialog", { name: "GET /api/apps" });
    await user.click(within(drawer).getByRole("button", { name: "Send request" }));
    expect(await within(drawer).findByText("You may not list applications.")).toBeInTheDocument();
    expect(within(drawer).getByText("Ask for the viewer role.")).toBeInTheDocument();
  });

  it("starts from the filters in the address", { timeout: 30_000 }, async () => {
    await pageAt("/settings/api?tag=Jobs&method=POST");
    expect(await screen.findByText("1 of 3 routes")).toBeInTheDocument();
    expect(screen.getByText("/api/jobs/update")).toBeInTheDocument();
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("region", { name: "Rutas de la API" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
