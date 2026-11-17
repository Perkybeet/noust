import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PublicConnection, PublicExposure, PublicPlan } from "../../../api/queries/publicAccess";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { FIREWALL } from "../../server/testing";
import { databaseRoutes } from "../testFixtures";

const BASE = "/api/databases/databases/postgresql/example_production";
const PUBLIC = "/api/databases/public/postgresql/example_production";

const CONNECT = {
  engine: "postgresql",
  database: "example_production",
  username: "example_production",
  password_known: false,
  port: 5432,
  apps: [],
  tunnel: { server: "<server>", ssh_port: 22, ssh_user: "root", local_port: 15432, remote_port: 5432, command: "ssh -N -L 15432:127.0.0.1:5432 root@<server>", url: "postgresql://x:********@127.0.0.1:15432/example_production", clients: {} },
  exposed: [],
  listen: { setting: "listen_addresses", addresses: ["localhost"], loopback_only: true },
};

const PLAN: PublicPlan = {
  engine: "postgresql",
  database: "example_production",
  port: 5433,
  bind_address: "0.0.0.0",
  sources: ["203.0.113.7/32"],
  tls: "required",
  profile: "owner",
  remote_user: "example_production_remote",
  unit: "noust-dbexpose-postgresql-5433",
  target: "127.0.0.1:5432",
  firewall: { backend: "ufw", active: true, manages: true, sources: ["203.0.113.7/32"], port: 5433 },
  warnings: [
    "PostgreSQL database example_production becomes reachable on port 5433 (every address) by 203.0.113.7/32: whoever can reach the port can try the account's password.",
    "The account owns the database: it can create, change and drop everything in it.",
  ],
};

const EXPOSURE: PublicExposure = {
  engine: "postgresql",
  database: "example_production",
  port: 5433,
  bind_address: "0.0.0.0",
  sources: ["203.0.113.7/32"],
  tls: "required",
  remote_user: "example_production_remote",
  profile: "read_only",
  unit: "noust-dbexpose-postgresql-5433",
  state: "exposed",
  created_at: "2026-10-06T10:00:00+00:00",
  created_by: "token",
  listening: true,
  firewall: { backend: "ufw", active: true, rules: [{ source: "203.0.113.7/32", created: true }] },
};

const CONNECTION: PublicConnection = {
  host: "203.0.113.50",
  port: 5433,
  database: "example_production",
  username: "example_production_remote",
  password: null,
  tls: "required",
  url: "postgresql://example_production_remote:********@203.0.113.50:5433/example_production?sslmode=require",
  options: { sslmode: "require" },
};

async function connectAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      [`GET ${BASE}/connect`]: () => json(200, CONNECT),
      [`GET ${PUBLIC}`]: () => json(200, { public: false }),
      "POST /api/databases/public/plan": () => json(200, PLAN),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/postgresql/example_production/connect");
  await screen.findByRole("heading", { level: 2, name: "Public access" });
  return { ...harness, backend };
}

describe("a database's public access", () => {
  it("is private until it is opened, and says how it is reached meanwhile", async () => {
    await connectAt();
    const section = await screen.findByRole("region", { name: "Public access" });
    expect(await within(section).findByText("Private")).toBeInTheDocument();
    expect(within(section).getByText(/does not listen beyond this server/)).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Open access…" })).toBeInTheDocument();
    await expectNoAxeViolations(section);
  });

  it("opens in two steps: what to open, then what Noust will do and what is accepted", async () => {
    const opened = { exposure: EXPOSURE, connection: { ...CONNECTION, password: "s3cret-Pa55" }, warnings: PLAN.warnings };
    const { user, backend } = await connectAt({
      "POST /api/databases/public": () => json(201, opened),
    });
    await user.click(await screen.findByRole("button", { name: "Open access…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open access to example_production" });
    await user.type(within(dialog).getByLabelText(/^Port/), "5433");
    await user.type(within(dialog).getByLabelText(/^Reachable from/), "203.0.113.7");
    await user.click(within(dialog).getByRole("radio", { name: /^Read only/ }));
    await user.click(within(dialog).getByRole("button", { name: "Review" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/public/plan")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/public/plan")[0]?.body).toEqual({
      engine: "postgresql",
      database: "example_production",
      port: 5433,
      bind_address: null,
      sources: ["203.0.113.7"],
      tls: "required",
      profile: "read_only",
      confirm: false,
    });
    // What the server warns about is on screen as it wrote it.
    expect(await within(dialog).findByText(/whoever can reach the port can try the account's password/)).toBeInTheDocument();
    expect(within(dialog).getByText("example_production_remote · Owner")).toBeInTheDocument();
    expect(within(dialog).getByText("ufw is told to allow the port")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);

    // Nothing is made before the warnings are accepted.
    await user.click(within(dialog).getByRole("button", { name: "Open access" }));
    expect(await within(dialog).findByText("Tick the box to accept what is listed above.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/public")).toHaveLength(0);

    await user.click(within(dialog).getByRole("checkbox", { name: "I understand this and want the database public" }));
    await user.click(within(dialog).getByRole("button", { name: "Open access" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/public")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/public")[0]?.body).toMatchObject({ confirm: true, port: 5433, profile: "read_only" });

    // The password comes back this once, in a dialog that forgets it when closed.
    const secret = await screen.findByRole("dialog", { name: "Connection to example_production" });
    expect(within(secret).getByRole("textbox", { name: "Password" })).toHaveValue("s3cret-Pa55");
    await user.click(within(secret).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByDisplayValue("s3cret-Pa55")).not.toBeInTheDocument());
  });

  // There is one way to open a port. The dialog that opens a service says, at its plan, that a rule letting everyone in on the port leaves
  // "only these addresses" with nothing to do (item 139); the one that makes a database public said nothing of it, and the same warning is the
  // same piece here, at the review, where the port is known.
  it("warns at the review, as opening a port does, that a firewall rule for everyone leaves it open to everyone", async () => {
    const rules = [
      { id: "db-any", backend: "ufw", action: "allow", ports: [[5433, 5433]], proto: "tcp", source: "any", spec: "5433/tcp ALLOW IN Anywhere", comment: "temporary: migration from the old host", noust: false, known: true, service: null, source_changeable: true },
      { id: "web", backend: "ufw", action: "allow", ports: [[80, 80], [443, 443]], proto: "tcp", source: "any", spec: "80,443/tcp ALLOW IN Anywhere", comment: "", noust: false, known: true, service: null, source_changeable: false },
    ];
    const { user } = await connectAt({
      "GET /api/server/security/firewall": () => json(200, { ...FIREWALL, firewall: { ...FIREWALL.firewall, rules }, protected_ports: [{ port: 22, reason: "SSH" }] }),
    });
    await user.click(await screen.findByRole("button", { name: "Open access…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open access to example_production" });
    await user.type(within(dialog).getByLabelText(/^Reachable from/), "203.0.113.7");
    await user.click(within(dialog).getByRole("button", { name: "Review" }));

    expect(await within(dialog).findByText("Port 5433 stays open to everyone")).toBeInTheDocument();
    expect(within(dialog).getByText("5433/tcp ALLOW IN Anywhere")).toBeInTheDocument();
    expect(within(dialog).getByText("temporary: migration from the old host")).toBeInTheDocument();
    // A rule for other ports is not this port's: it is not listed.
    expect(within(dialog).queryByText("80,443/tcp ALLOW IN Anywhere")).not.toBeInTheDocument();
    // This call opens a relay and has no step for a rule it did not write, so the way to take it out is the firewall's own page, not a tick.
    expect(within(dialog).getByRole("link", { name: "Take it out from the firewall's rules" })).toHaveAttribute("href", "/network/firewall");
    expect(within(dialog).queryByRole("checkbox", { name: /Remove it in the same step/ })).not.toBeInTheDocument();
  });

  it("says nothing of it when nobody is let in on that port, or when the firewall cannot be read", async () => {
    const { user } = await connectAt();
    await user.click(await screen.findByRole("button", { name: "Open access…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open access to example_production" });
    await user.type(within(dialog).getByLabelText(/^Reachable from/), "203.0.113.7");
    await user.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText(/whoever can reach the port can try the account's password/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/stays open to everyone/)).not.toBeInTheDocument();
  });

  it("refuses an address that is not one, before asking the server", async () => {
    const { user, backend } = await connectAt();
    await user.click(await screen.findByRole("button", { name: "Open access…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open access to example_production" });
    await user.type(within(dialog).getByLabelText(/^Reachable from/), "anyone-at-all");
    await user.type(within(dialog).getByLabelText(/^Port/), "80");
    await user.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText("Not an address or a network: anyone-at-all.")).toBeInTheDocument();
    expect(within(dialog).getByText("Use a whole number from 1024 to 65535.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/public/plan")).toHaveLength(0);
  });

  it("puts a refusal about TLS under TLS, in the server's words", async () => {
    const { user } = await connectAt({
      "POST /api/databases/public/plan": () =>
        problem(400, "validation_error", "PostgreSQL has ssl = off, so no client can use TLS here.", {
          hint: "Turn it on in postgresql.conf.",
          fields: { tls: "PostgreSQL has ssl = off, so no client can use TLS here." },
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Open access…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open access to example_production" });
    await user.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText("PostgreSQL has ssl = off, so no client can use TLS here.")).toBeInTheDocument();
    // Still on the first step, with the choice to make.
    expect(within(dialog).getByRole("radio", { name: /^Optional/ })).toBeInTheDocument();
  });


  it("shows what is open, shows the connection again, and makes it private after asking", async () => {
    let isPublic = true;
    const { user, backend } = await connectAt({
      [`GET ${PUBLIC}`]: () => json(200, isPublic ? { public: true, exposure: EXPOSURE, connection: CONNECTION } : { public: false }),
      [`POST ${PUBLIC}/reveal`]: () => json(200, { ...CONNECTION, password: "again-Pa55" }),
      [`DELETE ${PUBLIC}`]: () => {
        isPublic = false;
        return json(200, { engine: "postgresql", database: "example_production", removed: ["firewall", "proxy", "account"], already_private: false });
      },
    });
    const section = await screen.findByRole("region", { name: "Public access" });
    expect(await within(section).findByText("Public")).toBeInTheDocument();
    expect(within(section).getByText("203.0.113.50:5433")).toBeInTheDocument();
    expect(within(section).getByText("Required: a connection without TLS is refused")).toBeInTheDocument();
    expect(within(section).getByText("ufw allows the port from 203.0.113.7/32")).toBeInTheDocument();
    expect(within(section).getByText("Read only")).toBeInTheDocument();
    await expectNoAxeViolations(section);

    await user.click(within(section).getByRole("button", { name: "Show the connection" }));
    const secret = await screen.findByRole("dialog", { name: "Connection to example_production" });
    expect(within(secret).getByRole("textbox", { name: "Password" })).toHaveValue("again-Pa55");
    await user.click(within(secret).getByRole("button", { name: "Done" }));

    await user.click(within(section).getByRole("button", { name: "Close access" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Close access to example_production" });
    expect(within(confirm).getByText(/The database and its data stay/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Close access" }));
    await waitFor(() => expect(backend.callsTo(`DELETE ${PUBLIC}`)).toHaveLength(1));
    expect(await within(section).findByText("Private")).toBeInTheDocument();
  });

  it("says a half-closed exposure is still there, and what finishes it", async () => {
    await connectAt({
      [`GET ${PUBLIC}`]: () => json(200, { public: true, exposure: { ...EXPOSURE, state: "closing" }, connection: CONNECTION }),
    });
    const section = await screen.findByRole("region", { name: "Public access" });
    expect(await within(section).findByText("Being made private")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Show the connection" })).not.toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Close access" })).toBeInTheDocument();
  });

  it("says when the port stopped answering", async () => {
    await connectAt({
      [`GET ${PUBLIC}`]: () => json(200, { public: true, exposure: { ...EXPOSURE, listening: false }, connection: CONNECTION }),
    });
    expect(await screen.findByText("Not answering")).toBeInTheDocument();
  });
});
