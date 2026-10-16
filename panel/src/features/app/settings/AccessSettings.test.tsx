import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";
import { DOMAIN, ELEVATED, saveBar, renderSettings } from "./testKit";

const FIELDS = {
  basic_auth: false,
  realm: "Restricted",
  access: "all",
  allow_ips: [],
  deny_ips: [],
  real_ip: "off",
  real_ip_header: "",
  trusted_proxies: [],
  rate_limit: "",
  rate_burst: 20,
  connection_limit: 0,
  headers: {},
  hidden_headers: [],
  hsts: "default",
  noindex: false,
  canonical: "",
  routes: [],
};

function policy(changes: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    app: DOMAIN,
    policy: { ...FIELDS, ...changes },
    users: ["ana"],
    webserver: "nginx",
    applicable: true,
    reason: "",
    headers: [{ name: "X-Frame-Options", value: "SAMEORIGIN" }],
    real_ip_networks: ["173.245.48.0/20", "103.21.244.0/22"],
    real_ip_source: "built-in",
    ...extra,
  };
}

async function access(routes: Record<string, RouteHandler> = {}, body: object = policy()) {
  screenWidth(1440);
  const harness = await renderSettings("/access", "Access", undefined, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, body),
    ...routes,
  });
  // The policy is read after the page has its heading.
  await screen.findByRole("checkbox", { name: /Ask for a password/ });
  return harness;
}

describe("an application's access settings", () => {
  it("shows the password, the accounts, the lists, the limits and what the site sends", { timeout: 20_000 }, async () => {
    await access();

    expect(screen.getByRole("checkbox", { name: /Ask for a password/ })).not.toBeChecked();
    const accounts = screen.getByRole("list", { name: `Accounts of ${DOMAIN}` });
    expect(within(accounts).getByText("ana")).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("");
    expect(screen.getByLabelText("Burst")).toHaveValue("20");
    expect(screen.getByText("X-Frame-Options: SAMEORIGIN")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("sends only what changed, and then has nothing left to save", { timeout: 20_000 }, async () => {
    let stored = policy();
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as Record<string, unknown>;
        stored = policy({ allow_ips: sent["allow_ips"] ?? [], noindex: sent["noindex"] ?? false });
        return json(200, stored);
      },
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "203.0.113.0/24{enter}198.51.100.7");
    await user.click(screen.getByRole("checkbox", { name: /Ask search engines not to index/ }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      allow_ips: ["203.0.113.0/24", "198.51.100.7"],
      noindex: true,
    });
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("203.0.113.0/24\n198.51.100.7");
  });

  it("asks before it saves lists that would refuse the operator's own address, and says which", async () => {
    let calls = 0;
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        calls += 1;
        if ((call.body as Record<string, unknown>)["accept_lockout"] === true) return json(200, policy({ allow_ips: ["10.0.0.1"] }));
        return problem(400, "validation_error", "These lists would refuse your own address, 198.51.100.4", {
          fields: { allow_ips: "These lists would refuse your own address, 198.51.100.4" },
        });
      },
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "10.0.0.1");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("alertdialog", { name: "These lists would refuse your own address" });
    expect(within(dialog).getByText(/198\.51\.100\.4 is the address you are using now/)).toBeInTheDocument();
    // It is a question, not a red error beside the field.
    expect(screen.queryByText("These lists would refuse your own address, 198.51.100.4")).not.toBeInTheDocument();
    expect(calls).toBe(1);

    await user.click(within(dialog).getByRole("button", { name: "Save and risk the lockout" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(2);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body).toEqual({ accept_lockout: true, allow_ips: ["10.0.0.1"] });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("declining the lockout keeps the lists as typed and sends nothing more", async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () =>
        problem(400, "validation_error", "These lists would refuse your own address, 198.51.100.4", {
          fields: { allow_ips: "These lists would refuse your own address, 198.51.100.4" },
        }),
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "10.0.0.1");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("10.0.0.1");
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
  });

  it("shows a refusal beside the field it is about, in the server's words", async () => {
    const { user } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () =>
        problem(400, "validation_error", "bad network", { fields: { allow_ips: "999.1.1.1 is not an address or a network" } }),
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "999.1.1.1");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("999.1.1.1 is not an address or a network")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("tells a mistake it can see before asking the server, and sends nothing", async () => {
    const { user, backend } = await access();

    await user.type(screen.getByLabelText("Requests per client"), "fast");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Use a whole number of requests, 1 or more, or leave it empty for no limit.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
  });

  it("adds an account with a password the server hashes, and shows it in the list", async () => {
    let users = ["ana"];
    const { user, backend } = await access({
      [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, policy({}, { users })),
      [`PUT /api/apps/${DOMAIN}/policy/users/bob`]: () => {
        users = ["ana", "bob"];
        return json(200, policy({}, { users }));
      },
    });

    await user.click(screen.getByRole("button", { name: "Add account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an account" });
    await user.type(within(dialog).getByLabelText("Name"), "bob");
    await user.type(within(dialog).getByLabelText("Password"), "correct horse battery");
    expect(within(dialog).getByLabelText("Password")).toHaveAttribute("type", "password");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy/users/bob`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy/users/bob`)[0]?.body).toEqual({ password: "correct horse battery" });
    const accounts = screen.getByRole("list", { name: `Accounts of ${DOMAIN}` });
    expect(await within(accounts).findByText("bob")).toBeInTheDocument();
    // The password is not anywhere on the page once the dialog has closed.
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add an account" })).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain("correct horse battery");
  });

  it("refuses a login name the server would refuse, without asking it", async () => {
    const { user, backend } = await access();

    await user.click(screen.getByRole("button", { name: "Add account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an account" });
    await user.type(within(dialog).getByLabelText("Name"), "a b");
    await user.type(within(dialog).getByLabelText("Password"), "correct horse battery");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    expect(await within(dialog).findByText("Letters, digits and . _ @ - only.", { selector: "span" })).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT" && call.path.includes("/policy/users/"))).toHaveLength(0);
  });

  it("removes an account after asking, and warns when it is the only one a password needs", async () => {
    let users = ["ana"];
    const { user, backend } = await access(
      {
        [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ basic_auth: true }, { users })),
        [`DELETE /api/apps/${DOMAIN}/policy/users/ana`]: () => {
          users = [];
          return json(200, policy({ basic_auth: true }, { users }));
        },
      },
      policy({ basic_auth: true }),
    );

    await user.click(screen.getByRole("button", { name: "Remove: ana" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove ana" });
    expect(within(dialog).getByText(/It is the only account and the site asks for a password/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/policy/users/ana`)).toHaveLength(1);
    });
    expect(await screen.findByText("No accounts yet.")).toBeInTheDocument();
  });

  it("shows the limits only for nginx, which is the only server that has them", async () => {
    await access({}, policy({}, { webserver: "apache" }));

    expect(screen.queryByLabelText("Requests per client")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Allowed addresses")).toBeInTheDocument();
  });

  it("says why a policy does not reach the site, in the server's words", async () => {
    await access({}, policy({}, { applicable: false, reason: "A static site served by Apache has no password support here." }));

    expect(screen.getByText("This policy does not reach the site")).toBeInTheDocument();
    expect(screen.getByText("A static site served by Apache has no password support here.")).toBeInTheDocument();
  });

  it("names the ranges Cloudflare's address option trusts, and asks for the header of a proxy of its own", async () => {
    const { user } = await access();

    expect(screen.getByText(/Trust the header Cloudflare sends, from the networks it publishes: 2 \(built in\)\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("Header that carries the address")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Another proxy tells it/ }));
    expect(screen.getByLabelText("Header that carries the address")).toBeInTheDocument();
    expect(screen.getByLabelText("Trusted proxies")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await access();

    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { level: 2, name: "Acceso" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Pedir contraseña/ })).toBeInTheDocument();
  });

  it("lists the routes of the site, each with what it does", async () => {
    await access(
      {},
      policy({
        routes: [
          { path: "/api", kind: "proxy", auth: "inherit", port: 4000, strip: true, to: "", code: 301, keep_path: true, headers: {} },
          { path: "/admin", kind: "site", auth: "password", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: { "X-A": "1" } },
          { path: "/docs", kind: "redirect", auth: "inherit", port: 0, strip: false, to: "https://docs.example.org/", code: 308, keep_path: true, headers: {} },
        ],
      }),
    );

    const routes = screen.getByRole("list", { name: `Routes of ${DOMAIN}` });
    expect(within(routes).getByText("served by port 4000, prefix removed")).toBeInTheDocument();
    expect(within(routes).getByText("served as the site · password asked · headers: X-A")).toBeInTheDocument();
    expect(within(routes).getByText("308 to https://docs.example.org/")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("adds a route in a dialog and saves it with the rest of the form", { timeout: 20_000 }, async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as { routes: object[] };
        return json(200, policy({ routes: sent.routes }));
      },
    });
    expect(screen.getByText("No routes. The whole site answers the same way.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add a route" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a route" });
    await user.type(within(dialog).getByLabelText("Prefix"), "/api/");
    await user.click(within(dialog).getByRole("radio", { name: /Another service/ }));
    await user.type(within(dialog).getByLabelText("Port"), "4000");
    await user.click(within(dialog).getByRole("checkbox", { name: /Take the prefix off the path/ }));
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));

    const routes = await screen.findByRole("list", { name: `Routes of ${DOMAIN}` });
    expect(within(routes).getByText("served by port 4000, prefix removed")).toBeInTheDocument();
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      routes: [{ path: "/api", kind: "proxy", auth: "inherit", port: 4000, strip: true, to: "", code: 301, keep_path: true, headers: {} }],
    });
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
  });

  it("tells a route it cannot send before sending anything", async () => {
    const { user, backend } = await access();

    await user.click(screen.getByRole("button", { name: "Add a route" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a route" });
    await user.type(within(dialog).getByLabelText("Prefix"), "admin");
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));

    expect(await within(dialog).findByText(/Use a path such as \/admin/)).toBeInTheDocument();
    expect(within(dialog).getByText("A route of the site must ask for a password or lift it, or send a header.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("edits a route in place and removes another", async () => {
    const { user } = await access(
      {},
      policy({
        routes: [
          { path: "/admin", kind: "site", auth: "password", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: {} },
          { path: "/hooks", kind: "site", auth: "open", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: {} },
        ],
      }),
    );

    await user.click(screen.getByRole("button", { name: "Remove /hooks" }));
    expect(within(screen.getByRole("list", { name: `Routes of ${DOMAIN}` })).queryByText("/hooks")).not.toBeInTheDocument();
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit /admin" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit the route /admin" });
    expect(within(dialog).getByLabelText("Prefix")).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));
    // Nothing differs from what is stored except the removed route.
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("shows the server's refusal of the routes in its own words", async () => {
    const { user } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () =>
        problem(400, "validation_error", "bad route", { fields: { routes: "/api: port 22 is SSH's" } }),
    });

    await user.click(screen.getByRole("button", { name: "Add a route" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a route" });
    await user.type(within(dialog).getByLabelText("Prefix"), "/api");
    await user.click(within(dialog).getByRole("radio", { name: /Another service/ }));
    await user.type(within(dialog).getByLabelText("Port"), "22");
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("/api: port 22 is SSH's")).toBeInTheDocument();
  });
});
