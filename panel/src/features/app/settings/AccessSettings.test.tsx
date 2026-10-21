import { act, screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { openAccess, policy, privateStatus, TAILNET } from "./accessKit";
import { DOMAIN, saveBar } from "./testKit";

const access = openAccess;

/** Opens a folded part of the page, unless the application already uses it and it came open. */
async function openFold(user: UserEvent, name: RegExp): Promise<void> {
  const fold = await screen.findByRole("button", { name });
  if (fold.getAttribute("aria-expanded") === "false") await user.click(fold);
}

describe("an application's access settings", () => {
  it("shows the password, the accounts and the lists", { timeout: 20_000 }, async () => {
    await access();

    expect(screen.getByRole("checkbox", { name: /Ask for a password/ })).not.toBeChecked();
    const accounts = screen.getByRole("list", { name: `Accounts of ${DOMAIN}` });
    expect(within(accounts).getByText("ana")).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("");
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("sends only what changed, and then has nothing left to save", { timeout: 20_000 }, async () => {
    let stored = policy();
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as Record<string, unknown>;
        stored = policy({ allow_ips: sent["allow_ips"] ?? [], basic_auth: sent["basic_auth"] ?? false });
        return json(200, stored);
      },
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "203.0.113.0/24{enter}198.51.100.7");
    await user.click(screen.getByRole("checkbox", { name: /Ask for a password/ }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      accept_unreachable_auth: false,
      allow_ips: ["203.0.113.0/24", "198.51.100.7"],
      basic_auth: true,
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
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body).toEqual({ accept_lockout: true, accept_unreachable_auth: false, allow_ips: ["10.0.0.1"] });
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

  it("says why a policy does not reach the site, in the server's words", async () => {
    await access({}, policy({}, { applicable: false, reason: "A static site served by Apache has no password support here." }));

    expect(screen.getByText("This policy does not reach the site")).toBeInTheDocument();
    expect(screen.getByText("A static site served by Apache has no password support here.")).toBeInTheDocument();
  });

  it("names the ranges Cloudflare's address option trusts, and asks for the header of a proxy of its own", async () => {
    const { user } = await access();

    await openFold(user, /^The visitor's address/);
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
      accept_unreachable_auth: false,
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

  it("fills the sign-in service from a preset and sends it as one field", async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as { forward_auth: object };
        return json(200, policy({ forward_auth: sent.forward_auth }));
      },
    });

    await openFold(user, /^Sign-in service/);
    await user.click(await screen.findByRole("combobox", { name: "Start from" }));
    await user.click(await screen.findByRole("option", { name: "oauth2-proxy" }));
    expect(screen.getByLabelText("Address that is asked")).toHaveValue("http://127.0.0.1:4180/oauth2/auth");
    expect(screen.getByText("Run oauth2-proxy with --set-xauthrequest.")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      accept_unreachable_auth: false,
      forward_auth: { url: "http://127.0.0.1:4180/oauth2/auth", sign_in: "/oauth2/start?rd=", headers: ["X-Auth-Request-User", "X-Auth-Request-Email"] },
    });
  });

  it("tells an address that is not one before asking the server", async () => {
    const { user, backend } = await access();

    await openFold(user, /^Sign-in service/);
    await user.type(await screen.findByLabelText("Address that is asked"), "127.0.0.1:4180/auth");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Use an address that starts with http:// or https://.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
  });

  it("asks before saving a sign-in service that does not answer, with what was found", async () => {
    let calls = 0;
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        calls += 1;
        if ((call.body as Record<string, unknown>)["accept_unreachable_auth"] === true) return json(200, policy({ forward_auth: { url: "http://127.0.0.1:4180/auth", sign_in: "", headers: [] } }));
        return problem(400, "validation_error", "The authentication service at http://127.0.0.1:4180/auth does not answer: nothing accepts connections there", {
          fields: { forward_auth: "The authentication service at http://127.0.0.1:4180/auth does not answer: nothing accepts connections there" },
        });
      },
    });

    await openFold(user, /^Sign-in service/);
    await user.type(await screen.findByLabelText("Address that is asked"), "http://127.0.0.1:4180/auth");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("alertdialog", { name: "The sign-in service does not answer" });
    expect(within(dialog).getByText(/nothing accepts connections there/)).toBeInTheDocument();
    // A question, not a red error beside the field.
    expect(calls).toBe(1);
    await user.click(within(dialog).getByRole("button", { name: "Save anyway" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(2);
    });
    expect((backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body as Record<string, unknown>)["accept_unreachable_auth"]).toBe(true);
  });

  it("turns the sign-in service off by emptying its address", async () => {
    const stored = policy({ forward_auth: { url: "http://127.0.0.1:4180/auth", sign_in: "/oauth2/start?rd=", headers: ["X-A"] } });
    const { user, backend } = await access({ [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy()) }, stored);

    await openFold(user, /^Sign-in service/);
    await user.clear(await screen.findByLabelText("Address that is asked"));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect((backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body as Record<string, unknown>)["forward_auth"]).toEqual({ url: "", sign_in: "/oauth2/start?rd=", headers: ["X-A"] });
  });

  describe("the private network card", () => {
    it("offers the networks the server is on and publishes to the one chosen", { timeout: 20_000 }, async () => {
      let stored = privateStatus();
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/private`]: () => json(200, stored),
        [`PUT /api/apps/${DOMAIN}/private`]: () => {
          stored = privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs });
          return json(200, stored);
        },
      });

      const card = (await screen.findByRole("heading", { name: "Private network" })).closest("section");
      if (!card) throw new Error("no card");
      expect(await within(card).findByText(/Not private: it answers everybody/)).toBeInTheDocument();
      await user.click(within(card).getByRole("button", { name: "Answer only this network" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)[0]?.body).toEqual({ network: "tailscale", cidrs: [], accept_lockout: false });
      expect(await within(card).findByText("Private: only tailscale are let in.")).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "Answer everybody again" })).toBeInTheDocument();
      await expectNoAxeViolations(document.body);
    });

    it("asks before it publishes to a network the operator is not on", { timeout: 20_000 }, async () => {
      const { user, backend } = await access({
        [`PUT /api/apps/${DOMAIN}/private`]: (call) => {
          if ((call.body as Record<string, unknown>)["accept_lockout"] === true) return json(200, privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs }));
          return problem(400, "validation_error", "These lists would refuse your own address, 198.51.100.4", {
            fields: { allow_ips: "These lists would refuse your own address, 198.51.100.4" },
          });
        },
      });

      await user.click(await screen.findByRole("button", { name: "Answer only this network" }));
      const dialog = await screen.findByRole("alertdialog", { name: "These lists would refuse your own address" });
      expect(within(dialog).getByText(/198\.51\.100\.4 is the address you are using now/)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Save and risk the lockout" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)).toHaveLength(2);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)[1]?.body).toEqual({ network: "tailscale", cidrs: [], accept_lockout: true });
    });

    it("says so when the server is on no private network, and names a network that is down", async () => {
      await access({
        [`GET /api/apps/${DOMAIN}/private`]: () => json(200, privateStatus({ available: [{ ...TAILNET, up: false, problem: "Tailscale is Stopped" }], tailnet_name: "" })),
      });

      expect(await screen.findByText("This server is on no tailnet or WireGuard network Noust can see")).toBeInTheDocument();
      expect(screen.getByText("tailscale is not usable")).toBeInTheDocument();
      expect(screen.getByText("Tailscale is Stopped")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Answer only this network" })).not.toBeInTheDocument();
    });

    it("serves a private application with the tailnet's certificate when the server's administrator asks", { timeout: 20_000 }, async () => {
      let stored = privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs });
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/private`]: () => json(200, stored),
        [`POST /api/apps/${DOMAIN}/private/certificate`]: () => {
          stored = privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs, tailnet_certificate: "tailnet-web.tail1234.ts.net" });
          return json(200, { name: "tailnet-web.tail1234.ts.net", hostname: "web.tail1234.ts.net", not_after: "2027-01-01T00:00:00+00:00", applications: [DOMAIN] });
        },
      });

      expect(await screen.findByText("web.tail1234.ts.net")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Serve it with the tailnet's certificate" }));

      await waitFor(() => {
        expect(backend.callsTo(`POST /api/apps/${DOMAIN}/private/certificate`)).toHaveLength(1);
      });
      expect(await screen.findByText(/Served with tailnet-web\.tail1234\.ts\.net/)).toBeInTheDocument();
    });

    it("speaks Spanish", async () => {
      await access();
      await act(() => setLocale("es"));

      expect(await screen.findByRole("heading", { name: "Red privada" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Responder solo a esta red" })).toBeInTheDocument();
    });
  });
});
