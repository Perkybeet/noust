import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";

const SITE = {
  name: "shop.example.com",
  site_name: "shop.example.com",
  webserver: "nginx",
  enabled: true,
  config_path: "/etc/nginx/sites-available/shop.example.com",
  has_ssl: true,
  server_names: ["shop.example.com"],
  noust_managed: true,
  app: "shop.example.com",
};

function status(changes: Record<string, unknown> = {}) {
  return {
    webserver: "nginx",
    enabled: false,
    action: "",
    redirect_to: "",
    tls: false,
    file: "/etc/nginx/sites-available/00-noust-default",
    disabled_distribution_site: "",
    holders: [],
    version: "1.24.0",
    notes: [],
    ...changes,
  };
}

function sites(routes: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [SITE], total: 1, webserver: "nginx" }),
    "GET /api/unknown-hosts": () => json(200, status()),
    ...routes,
  });
  return { ...renderConsole("/domains/sites"), backend };
}

describe("what answers a name no site serves", { timeout: 20_000 }, () => {
  it("says the first site answers when nothing is set", async () => {
    sites();

    const card = (await screen.findByRole("heading", { name: "Names no site serves" })).closest("section");
    if (card === null) throw new Error("no card");
    expect(await within(card).findByText(/the first site the web server loaded answers/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Set the answer" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("shows what is set, over TLS too, and what it switched off", async () => {
    sites({
      "GET /api/unknown-hosts": () =>
        json(200, status({ enabled: true, action: "refuse", tls: true, disabled_distribution_site: "default" })),
    });

    expect(await screen.findByText("The answer is 503.")).toBeInTheDocument();
    expect(screen.getByText(/The handshake is refused/)).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Take away" })).toBeInTheDocument();
  });

  it("names the site that holds the default, and does not hide the card", async () => {
    sites({
      "GET /api/unknown-hosts": () => json(200, status({ holders: [{ file: "/etc/nginx/conf.d/own.conf", port: 80, distribution: false }] })),
    });

    expect(await screen.findByText("Another site is the default")).toBeInTheDocument();
    expect(screen.getByText(/\/etc\/nginx\/conf\.d\/own\.conf already declares itself the default server for port 80/)).toBeInTheDocument();
  });

  it("sets a redirect from a dialog and shows it", async () => {
    let current = status();
    const { user, backend } = sites({
      "GET /api/unknown-hosts": () => json(200, current),
      "PUT /api/unknown-hosts": (call) => {
        const sent = call.body as Record<string, unknown>;
        current = status({ enabled: true, action: sent["action"], redirect_to: sent["redirect_to"], tls: true });
        return json(200, current);
      },
    });

    await user.click(await screen.findByRole("button", { name: "Set the answer" }));
    const dialog = await screen.findByRole("dialog", { name: "Answer to unknown names" });
    await user.click(within(dialog).getByRole("radio", { name: /A redirect/ }));
    await user.type(within(dialog).getByLabelText("Redirect to"), "https://example.org/");
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/unknown-hosts")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/unknown-hosts")[0]?.body).toEqual({
      action: "redirect",
      redirect_to: "https://example.org/",
      switch_off_distribution_site: false,
      webserver: "nginx",
    });
    expect(await screen.findByText("The answer is a permanent redirect to https://example.org/.")).toBeInTheDocument();
  });

  it("will not send a redirect without an address", async () => {
    const { user, backend } = sites();

    await user.click(await screen.findByRole("button", { name: "Set the answer" }));
    const dialog = await screen.findByRole("dialog", { name: "Answer to unknown names" });
    await user.click(within(dialog).getByRole("radio", { name: /A redirect/ }));
    await user.type(within(dialog).getByLabelText("Redirect to"), "example.org");
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(await within(dialog).findByText("Use an address that starts with http:// or https://.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/unknown-hosts")).toHaveLength(0);
  });

  it("offers to switch off the web server's own default site, and says it is put back", async () => {
    const { user, backend } = sites({
      "GET /api/unknown-hosts": () =>
        json(200, status({ holders: [{ file: "/etc/nginx/sites-enabled/default", port: 80, distribution: true }] })),
      "PUT /api/unknown-hosts": () => json(200, status({ enabled: true, action: "close", disabled_distribution_site: "default" })),
    });

    await user.click(await screen.findByRole("button", { name: "Set the answer" }));
    const dialog = await screen.findByRole("dialog", { name: "Answer to unknown names" });
    await user.click(within(dialog).getByRole("checkbox", { name: /Switch off the web server's own default site/ }));
    expect(within(dialog).getByText(/switched on again when this answer is taken away/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/unknown-hosts")).toHaveLength(1);
    });
    expect((backend.callsTo("PUT /api/unknown-hosts")[0]?.body as Record<string, unknown>)["switch_off_distribution_site"]).toBe(true);
  });

  it("shows the web server's own refusal in the dialog", async () => {
    const { user } = sites({
      "PUT /api/unknown-hosts": () => problem(400, "validation_error", "nginx refused the site for unknown names", { output: "nginx: [emerg] bad thing" }),
    });

    await user.click(await screen.findByRole("button", { name: "Set the answer" }));
    const dialog = await screen.findByRole("dialog", { name: "Answer to unknown names" });
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(await within(dialog).findByText(/nginx: \[emerg\] bad thing/)).toBeInTheDocument();
  });

  it("takes the answer away", async () => {
    let current = status({ enabled: true, action: "close", tls: true });
    const { user, backend } = sites({
      "GET /api/unknown-hosts": () => json(200, current),
      "DELETE /api/unknown-hosts": () => {
        current = status();
        return json(200, current);
      },
    });

    await user.click(await screen.findByRole("button", { name: "Take away" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/unknown-hosts")).toHaveLength(1);
    });
    expect(await screen.findByText(/the first site the web server loaded answers/)).toBeInTheDocument();
  });

  it("says it in Spanish", async () => {
    await act(() => setLocale("es"));
    sites();

    expect(await screen.findByRole("heading", { name: "Nombres que ningún sitio sirve" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Fijar la respuesta" })).toBeInTheDocument();
  });
});
