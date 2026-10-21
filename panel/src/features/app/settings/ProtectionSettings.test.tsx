import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { certificate, clientCerts, HTTP3, OLD_SERIAL, openProtection, policy, SERIAL, WAF } from "./accessKit";
import { DOMAIN, saveBar } from "./testKit";

const protection = openProtection;

describe("an application's protection settings", () => {
  it("tells a mistake it can see before asking the server, and sends nothing", async () => {
    const { user, backend } = await protection();

    await user.type(screen.getByLabelText("Requests per client"), "fast");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Use a whole number of requests, 1 or more, or leave it empty for no limit.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
  });

  it("shows the limits only for nginx, which is the only server that has them", async () => {
    await protection({}, policy({}, { webserver: "apache" }));

    expect(screen.queryByLabelText("Requests per client")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Ask search engines not to index/ })).toBeInTheDocument();
  });

  it("turns HTTP/3 on from a checkbox and sends only that", async () => {
    const { user, backend } = await protection({
      [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ http3: true })),
    });

    await user.click(await screen.findByRole("checkbox", { name: /Serve this site over HTTP\/3/ }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, http3: true });
  });

  it("lets a site answer over HTTP as well from a checkbox that is on by default, and sends only that", async () => {
    const { user, backend } = await protection({
      [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ force_https: false })),
    });

    const forced = await screen.findByRole("checkbox", { name: /Send visitors on port 80 to HTTPS/ });
    expect(forced).toBeChecked();
    await user.click(forced);
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, force_https: false });
  });

  it("says what is missing when the server cannot serve HTTP/3, and offers no way to turn it on", async () => {
    await protection({
      [`GET /api/apps/${DOMAIN}/http3`]: () =>
        json(200, { ...HTTP3, available: false, version: "1.24.0", problem: "nginx 1.24.0 is older than 1.25.0, the first with HTTP/3", hint: "Install the nginx.org build." }),
    });

    expect(await screen.findByText("nginx 1.24.0 is older than 1.25.0, the first with HTTP/3")).toBeInTheDocument();
    expect(screen.getByText("Install the nginx.org build.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Serve this site over HTTP\/3/ })).toHaveAttribute("aria-disabled", "true");
  });

  it("warns when the firewall keeps UDP 443 out, with the command that opens it", async () => {
    await protection({
      [`GET /api/apps/${DOMAIN}/http3`]: () => json(200, { ...HTTP3, udp_allowed: false, firewall_hint: "ufw allow 443/udp" }),
    });

    expect(await screen.findByText("The firewall does not let UDP 443 in")).toBeInTheDocument();
    expect(screen.getByText("ufw allow 443/udp")).toBeInTheDocument();
  });

  it("does not offer HTTP/3 for a site Apache serves", async () => {
    await protection({}, policy({}, { webserver: "apache" }));

    expect(screen.queryByRole("checkbox", { name: /Serve this site over HTTP\/3/ })).not.toBeInTheDocument();
  });

  describe("the client certificates card", () => {
    const cardOf = async (): Promise<HTMLElement> => {
      const card = (await screen.findByRole("heading", { name: "Client certificates" })).closest("section");
      if (!card) throw new Error("no card");
      return card;
    };

    it("lists the certificates with their state and revokes one after asking", { timeout: 20_000 }, async () => {
      let revoked = false;
      const { user, backend } = await protection(
        {
          [`GET /api/apps/${DOMAIN}/client-certs`]: () =>
            json(200, clientCerts([certificate("ana-laptop"), certificate("bob-phone", { serial: OLD_SERIAL, revoked: revoked || true, revoked_at: "2026-10-02T00:00:00+00:00" })], true)),
          [`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`]: () => {
            revoked = true;
            return new Response(null, { status: 204 });
          },
        },
        policy({ client_certs: true }),
      );

      const card = await cardOf();
      const list = await within(card).findByRole("list", { name: `Client certificates of ${DOMAIN}` });
      expect(within(list).getByText("ana-laptop")).toBeInTheDocument();
      expect(within(list).getByText("Valid")).toBeInTheDocument();
      expect(within(list).getByText("Revoked")).toBeInTheDocument();
      expect(within(list).queryByRole("button", { name: "Revoke: bob-phone" })).not.toBeInTheDocument();

      await user.click(within(list).getByRole("button", { name: "Revoke: ana-laptop" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Revoke the certificate ana-laptop?" });
      // It is the only valid one of a site that asks for them, and the dialog says so.
      expect(within(dialog).getByText(/last valid certificate/)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

      await waitFor(() => {
        expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)).toHaveLength(1);
      });
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)[0]?.search.get("accept_lockout")).toBe("true");
    });

    it("does not warn about the last certificate when another is valid, and sends no lockout consent", { timeout: 20_000 }, async () => {
      const { user, backend } = await protection(
        {
          [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts([certificate("ana-laptop"), certificate("bob-phone", { serial: OLD_SERIAL })], true)),
          [`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`]: () => new Response(null, { status: 204 }),
        },
        policy({ client_certs: true }),
      );

      const card = await cardOf();
      await user.click(await within(card).findByRole("button", { name: "Revoke: ana-laptop" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).queryByText(/last valid certificate/)).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

      await waitFor(() => {
        expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)).toHaveLength(1);
      });
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)[0]?.search.get("accept_lockout")).toBe("false");
    });

    it("makes a certificate and shows the password and the downloads once", { timeout: 30_000 }, async () => {
      let made = false;
      const { user, backend } = await protection({
        [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts(made ? [certificate("ana-laptop")] : [])),
        [`POST /api/apps/${DOMAIN}/client-certs`]: () => {
          made = true;
          return json(201, {
            certificate: certificate("ana-laptop"),
            bundle: "MIIBAA==",
            password: "correct-horse-battery",
            certificate_pem: "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
            key_pem: "-----BEGIN PRIVATE KEY-----\nBBBB\n-----END PRIVATE KEY-----\n",
            ca_pem: "-----BEGIN CERTIFICATE-----\nCCCC\n-----END CERTIFICATE-----\n",
          });
        },
      });

      const card = await cardOf();
      expect(await within(card).findByText("No certificates yet.")).toBeInTheDocument();
      await user.click(within(card).getByRole("button", { name: "Create a certificate" }));
      const dialog = await screen.findByRole("dialog", { name: "Create a certificate" });
      await user.type(within(dialog).getByLabelText("Name"), "ana-laptop");
      await user.click(within(dialog).getByRole("button", { name: "Create a certificate" }));

      await waitFor(() => {
        expect(backend.callsTo(`POST /api/apps/${DOMAIN}/client-certs`)).toHaveLength(1);
      });
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/client-certs`)[0]?.body).toEqual({ name: "ana-laptop", days: 365 });
      const ready = await screen.findByRole("dialog", { name: "Certificate ana-laptop is ready" });
      expect(within(ready).getByText("correct-horse-battery")).toBeInTheDocument();
      expect(within(ready).getByText("This is the only time the key and the password are shown")).toBeInTheDocument();
      expect(within(ready).getByRole("button", { name: "Download the bundle (.p12)" })).toBeInTheDocument();
      expect(within(ready).getByRole("button", { name: "Download the key (.key)" })).toBeInTheDocument();
      await expectNoAxeViolations(document.body);

      await user.click(within(ready).getByRole("button", { name: "Done" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });
      // The password is gone with the window: nothing on the page holds it any more.
      expect(screen.queryByText("correct-horse-battery")).not.toBeInTheDocument();
      expect(await within(card).findByText("ana-laptop")).toBeInTheDocument();
    });

    it("refuses a name the server would refuse without asking it", { timeout: 20_000 }, async () => {
      const { user, backend } = await protection();

      const card = await cardOf();
      await user.click(within(card).getByRole("button", { name: "Create a certificate" }));
      const dialog = await screen.findByRole("dialog", { name: "Create a certificate" });
      await user.type(within(dialog).getByLabelText("Name"), "has space");
      await user.click(within(dialog).getByRole("button", { name: "Create a certificate" }));

      expect(await within(dialog).findAllByText(/Letters, digits and \. _ @ - only, up to 64\./)).not.toHaveLength(0);
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/client-certs`)).toHaveLength(0);
    });

    it("asks before it asks every visitor for a certificate nobody has", { timeout: 20_000 }, async () => {
      const message = `No client certificate of ${DOMAIN} is valid, so no one could open it`;
      const { user, backend } = await protection({
        [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
          if ((call.body as Record<string, unknown>)["accept_lockout"] === true) return json(200, policy({ client_certs: true }));
          return problem(400, "validation_error", message, { fields: { client_certs: message } });
        },
      });

      await user.click(screen.getByRole("checkbox", { name: /Ask every visitor for a certificate/ }));
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      const dialog = await screen.findByRole("alertdialog", { name: "No client certificate is valid yet" });
      // A question, not a red error beside the checkbox.
      expect(screen.queryByText(message)).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Save and shut everybody out" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(2);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, client_certs: true });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body).toEqual({ accept_lockout: true, accept_unreachable_auth: false, client_certs: true });
    });

    it("saves the requirement with the rest of the form when a certificate is valid", { timeout: 20_000 }, async () => {
      const { user, backend } = await protection({
        [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts([certificate("ana-laptop")])),
        [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ client_certs: true })),
      });

      await user.click(screen.getByRole("checkbox", { name: /Ask every visitor for a certificate/ }));
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, client_certs: true });
    });

    it("speaks Spanish", async () => {
      await protection();
      await act(() => setLocale("es"));

      expect(await screen.findByRole("heading", { name: "Certificados de cliente" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Crear un certificado" })).toBeInTheDocument();
    });
  });

  describe("the web application firewall card", () => {
    it("starts in detect mode when asked and sends only that", { timeout: 20_000 }, async () => {
      const { user, backend } = await protection({
        [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ waf: "detect" })),
      });

      const card = (await screen.findByRole("heading", { name: "Web application firewall" })).closest("section");
      if (!card) throw new Error("no card");
      expect(within(card).getByRole("radio", { name: /Off/ })).toBeChecked();
      await user.click(within(card).getByRole("radio", { name: /Detect/ }));
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, waf: "detect" });
    });

    it("lists what the rules saw, flags what block mode would refuse, and leaves a noisy rule out on a click", { timeout: 20_000 }, async () => {
      const { user, backend } = await protection(
        {
          [`GET /api/apps/${DOMAIN}/waf/events`]: () =>
            json(200, {
              events: [
                { time: "Sat Oct 17 01:21:33 2026", client: "203.0.113.9", host: DOMAIN, method: "GET", uri: "/search?q=select", status: 200, blocked: false, would_block: true, rules: [{ id: "942100", message: "SQL Injection", severity: "2" }, { id: "949110", message: "Score", severity: "2" }] },
                { time: "Sat Oct 17 01:21:30 2026", client: "198.51.100.4", host: DOMAIN, method: "GET", uri: "/?f=../../etc/passwd", status: 403, blocked: true, would_block: false, rules: [{ id: "930110", message: "Path Traversal", severity: "2" }, { id: "949110", message: "Score", severity: "2" }] },
              ],
              noisy: [{ id: "942100", message: "SQL Injection Attack Detected via libinjection", count: 4 }],
            }),
          [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ waf: "detect", waf_exclusions: [942100] })),
        },
        policy({ waf: "detect" }),
      );

      const list = await screen.findByRole("list", { name: "Requests the rules judged lately" });
      expect(within(list).getByText("GET /search?q=select")).toBeInTheDocument();
      expect(within(list).getByText("Block mode would refuse it")).toBeInTheDocument();
      expect(within(list).getByText("Refused")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Leave out 942100" }));
      expect(screen.getByLabelText("Rules left out for this site")).toHaveValue("942100");
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, waf_exclusions: [942100] });
      await expectNoAxeViolations(document.body);
    });

    it("says what the server is missing and installs it for the server's administrator", { timeout: 20_000 }, async () => {
      let available = false;
      const { user, backend } = await protection({
        [`GET /api/apps/${DOMAIN}/waf`]: () =>
          json(200, available ? WAF : { ...WAF, available: false, module_loaded: false, module_installed: false, rules: false, problem: "nginx has no ModSecurity module", hint: "Install it: noust app policy waf install" }),
        "POST /api/waf/install": () => {
          available = true;
          return json(200, { packages: WAF.packages });
        },
      });

      expect(await screen.findByText("nginx has no ModSecurity module")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Install it" }));

      await waitFor(() => {
        expect(backend.callsTo("POST /api/waf/install")).toHaveLength(1);
      });
      await waitFor(() => {
        expect(screen.queryByText("nginx has no ModSecurity module")).not.toBeInTheDocument();
      });
    });

    it("tells a rule number that is not one before asking the server", async () => {
      const { user, backend } = await protection({}, policy({ waf: "block" }));

      await user.type(await screen.findByLabelText("Rules left out for this site"), "942100{enter}abc");
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      expect(await screen.findByText("Rule numbers are whole numbers, such as 942100.")).toBeInTheDocument();
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
    });

    it("speaks Spanish", async () => {
      await protection();
      await act(() => setLocale("es"));

      expect(await screen.findByRole("heading", { name: "Cortafuegos de aplicaciones web" })).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: /Detectar/ })).toBeInTheDocument();
    });
  });
});
