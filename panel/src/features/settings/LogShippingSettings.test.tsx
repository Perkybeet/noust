import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const LOKI = {
  name: "grafana",
  kind: "loki",
  settings: { url: "https://logs.example.com", tenant: "acme", labels: { env: "prod" } },
  apps: ["shop.example.com"],
  enabled: true,
  has_secret: true,
  last_ok_at: "2026-10-13T10:00:00",
  last_error: null,
  last_error_at: null,
  shipped: 1520,
  plaintext: false,
};

const FAILING = {
  ...LOKI,
  name: "axiom-prod",
  kind: "axiom",
  settings: { url: "https://api.axiom.co", dataset: "noust" },
  apps: [],
  last_ok_at: null,
  last_error: "axiom answered HTTP 401: invalid token",
  last_error_at: "2026-10-13T10:05:00",
  shipped: 0,
};

const OFF = { ...LOKI, name: "old-syslog", kind: "syslog", settings: { transport: "tls", host: "logs.example.com", port: 6514 }, enabled: false, has_secret: false, plaintext: false };

const SERVICE = { installed: true, active: true, state: "active" };

function list(drains: object[], service: object = SERVICE) {
  return { drains, total: drains.length, service };
}

async function shipping(drains: object[] = [LOKI], extra: Record<string, RouteHandler> = {}, service: object = SERVICE) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/log-drains": () => json(200, list(drains, service)),
    ...extra,
  });
  const harness = renderConsole("/settings/log-shipping");
  await screen.findByRole("heading", { level: 2, name: "Log shipping" });
  return { ...harness, backend };
}

describe("where the logs of applications are shipped", () => {
  it("lists each destination with its kind, its applications, how it is doing and what it has sent", async () => {
    await shipping([LOKI, FAILING, OFF]);

    const list = await screen.findByRole("list", { name: "Destinations of logs" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    const row = (index: number): HTMLElement => {
      const found = rows[index];
      if (!found) throw new Error(`no row ${String(index)}`);
      return found;
    };
    expect(within(row(0)).getByText("grafana")).toBeInTheDocument();
    expect(within(row(0)).getByText("shop.example.com")).toBeInTheDocument();
    expect(within(row(0)).getByText("1,520 lines sent")).toBeInTheDocument();
    expect(within(row(1)).getByText("All applications")).toBeInTheDocument();
    expect(within(row(2)).getByText("Off")).toBeInTheDocument();
  });

  it("shows what a failing destination answered, verbatim", async () => {
    await shipping([FAILING]);

    expect(await screen.findByText("Failing")).toBeInTheDocument();
    expect(screen.getByText("axiom answered HTTP 401: invalid token", { selector: "pre" })).toBeInTheDocument();
  });

  it("warns about a destination whose lines travel in the clear", async () => {
    await shipping([{ ...LOKI, plaintext: true }]);
    expect(await screen.findByText("Not encrypted")).toBeInTheDocument();
  });

  it("says nothing is being sent when the shipper is not running", async () => {
    await shipping([LOKI], {}, { installed: false, active: false, state: "inactive" });
    expect(await screen.findByText("Nothing is being sent")).toBeInTheDocument();
  });

  it("does not say it when there is nothing to send", async () => {
    await shipping([], {}, { installed: false, active: false, state: "inactive" });
    expect(await screen.findByText("No destinations")).toBeInTheDocument();
    expect(screen.queryByText("Nothing is being sent")).not.toBeInTheDocument();
  });

  it("adds a Loki destination with its token, which is only written", async () => {
    const { user, backend } = await shipping([], {
      "PUT /api/log-drains/grafana": () => json(200, LOKI),
    });
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a destination" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "grafana");
    await user.type(within(dialog).getByRole("textbox", { name: "Address" }), "https://logs.example.com");
    await user.type(within(dialog).getByLabelText(/Token or password/), "glc_secret_token");
    await user.type(within(dialog).getByRole("textbox", { name: /Labels/ }), "env=prod");

    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/log-drains/grafana")[0]?.body).toEqual({
        kind: "loki",
        settings: { url: "https://logs.example.com", labels: { env: "prod" } },
        apps: [],
        enabled: true,
        secret: "glc_secret_token",
        keep_secret: false,
      });
    });
    expect(await screen.findByText("Saved grafana")).toBeInTheDocument();
  });

  it("needs a dataset and a token for Axiom, and says so where they go", async () => {
    const { user, backend } = await shipping([]);
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a destination" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "axiom");
    await user.click(within(dialog).getByRole("combobox", { name: "Kind" }));
    await user.click(await screen.findByRole("option", { name: "Axiom" }));

    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));

    expect(await within(dialog).findByText("Give the dataset.")).toBeInTheDocument();
    expect(within(dialog).getByText("An Axiom destination needs its API token.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("takes New Relic's region and license key, and asks for no address", async () => {
    const { user, backend } = await shipping([], {
      "PUT /api/log-drains/relic": () => json(200, OFF),
    });
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a destination" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "relic");
    await user.click(within(dialog).getByRole("combobox", { name: "Kind" }));
    await user.click(await screen.findByRole("option", { name: "New Relic" }));

    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));
    expect(within(dialog).getByText("A New Relic destination needs its license key.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);

    await user.click(within(dialog).getByRole("combobox", { name: "Region" }));
    await user.click(await screen.findByRole("option", { name: "Europe" }));
    await user.type(within(dialog).getByLabelText(/Token or password/), "eu01xx0123456789NRAL");
    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/log-drains/relic")[0]?.body).toMatchObject({
        kind: "newrelic",
        settings: { region: "eu" },
        secret: "eu01xx0123456789NRAL",
        keep_secret: false,
      });
    });
  });

  it("takes a remote syslog's host, transport and port instead of an address", async () => {
    const { user, backend } = await shipping([], {
      "PUT /api/log-drains/central": () => json(200, OFF),
    });
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a destination" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "central");
    await user.click(within(dialog).getByRole("combobox", { name: "Kind" }));
    await user.click(await screen.findByRole("option", { name: "Remote syslog" }));
    expect(within(dialog).queryByRole("textbox", { name: "Address" })).not.toBeInTheDocument();
    await user.type(within(dialog).getByRole("textbox", { name: "Host" }), "logs.example.com");
    await user.type(within(dialog).getByRole("textbox", { name: /Port/ }), "6514");

    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/log-drains/central")[0]?.body).toMatchObject({
        kind: "syslog",
        settings: { transport: "tls", host: "logs.example.com", port: 6514 },
      });
    });
  });

  it("edits a destination keeping the token it has when none is typed", async () => {
    const { user, backend } = await shipping([LOKI], {
      "PUT /api/log-drains/grafana": () => json(200, LOKI),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for grafana" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit grafana" });
    expect(within(dialog).getByRole("textbox", { name: "Name" })).toBeDisabled();
    expect(within(dialog).getByRole("textbox", { name: "Address" })).toHaveValue("https://logs.example.com");
    expect(within(dialog).getByText("Leave it blank to keep the one it has.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/log-drains/grafana")[0]?.body).toMatchObject({ kind: "loki", keep_secret: true, apps: ["shop.example.com"] });
    });
    expect(backend.callsTo("PUT /api/log-drains/grafana")[0]?.body).not.toHaveProperty("secret");
  });

  it("turns a destination off without touching its token", async () => {
    const { user, backend } = await shipping([LOKI], {
      "PUT /api/log-drains/grafana": () => json(200, { ...LOKI, enabled: false }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for grafana" }));
    await user.click(await screen.findByRole("menuitem", { name: "Turn off" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/log-drains/grafana")[0]?.body).toMatchObject({ enabled: false, keep_secret: true });
    });
  });

  it("sends a test line and says what to look for, or what the destination answered", async () => {
    const { user } = await shipping([LOKI, FAILING], {
      "POST /api/log-drains/grafana/test": () => json(200, { name: "grafana", sent: "Noust log drain test from web-01" }),
      "POST /api/log-drains/axiom-prod/test": () => problem(502, "delivery_error", "axiom answered HTTP 401", { output: "invalid token" }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for grafana" }));
    await user.click(await screen.findByRole("menuitem", { name: "Send a test line" }));
    expect(await screen.findByText("grafana accepted the test line")).toBeInTheDocument();
    expect(screen.getByText("Look for: Noust log drain test from web-01")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Actions for axiom-prod" }));
    await user.click(await screen.findByRole("menuitem", { name: "Send a test line" }));
    expect(await screen.findByText("axiom-prod did not accept the test line")).toBeInTheDocument();
  });

  it("removes a destination after one question", async () => {
    const { user, backend } = await shipping([LOKI], {
      "DELETE /api/log-drains/grafana": () => json(200, list([])),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for grafana" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop shipping logs to grafana?" });
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/log-drains/grafana")).toHaveLength(1);
    });
  });

  it("puts a refusal under the field it names", async () => {
    const { user } = await shipping([], {
      "PUT /api/log-drains/grafana": () => problem(422, "validationerror", "Not an address", { fields: { url: "A user or password in the address is not accepted" } }),
    });
    await user.click(await screen.findByRole("button", { name: "Add a destination" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a destination" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "grafana");
    await user.type(within(dialog).getByRole("textbox", { name: "Address" }), "https://u:p@logs.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Save destination" }));

    expect(await within(dialog).findByText("A user or password in the address is not accepted")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await shipping([LOKI, FAILING, OFF]);
    await screen.findByRole("list", { name: "Destinations of logs" });
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Añadir un destino" })).toBeInTheDocument();
  });
});
