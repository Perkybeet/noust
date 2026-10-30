import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json, NODES, problem } from "../../../test/fakes";
import { DS, NOT_INSTALLED, NOT_SET_UP, noustConsole, PRIMARY, SECONDARY, ZONE, ZONES } from "./noustFixtures";
import { noustState } from "./state";
import { glueOf } from "./ZonesPanel";

/** The first of several matches: the header's primary action, which an empty state may repeat. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("nothing matched");
  return item;
}

describe("noustState", () => {
  it("tells the five places a server can be in", () => {
    expect(noustState({ installed: false, configured: false, running: false, role: null })).toBe("notInstalled");
    expect(noustState({ installed: true, configured: false, running: false, role: null })).toBe("notSetUp");
    expect(noustState({ installed: true, configured: true, running: true, role: "primary" })).toBe("primary");
    expect(noustState({ installed: true, configured: true, running: true, role: "secondary" })).toBe("secondary");
    expect(noustState({ installed: true, configured: true, running: false, role: "primary" })).toBe("stopped");
  });
});

describe("glueOf", () => {
  it("reads one name server and its addresses per line, and skips what is not one", () => {
    expect(glueOf("ns1.example.com=203.0.113.10\n\nNS2.example.com = 198.51.100.7, 2001:db8::7\nnonsense\nns3.example.com=")).toEqual({
      "ns1.example.com": ["203.0.113.10"],
      "ns2.example.com": ["198.51.100.7", "2001:db8::7"],
    });
  });
});

describe("Noust DNS before it is installed", () => {
  it("says what running a name server takes on, in the console's words, and installs only after the acceptance", { timeout: 20_000 }, async () => {
    let queued = 0;
    const { user, backend } = noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, NOT_INSTALLED),
      "POST /api/dns/noust/server/install": () => {
        queued += 1;
        return json(202, { job_id: "j1", status: "queued", message: "queued" });
      },
    });

    expect(await screen.findByRole("heading", { level: 1, name: "Noust DNS" })).toBeInTheDocument();
    expect(await screen.findByText(/the whole internet depends on: if it is down, every name it serves is down/)).toBeInTheDocument();
    expect(screen.getByText(/can be used to flood other people with traffic/)).toBeInTheDocument();

    // Pressing Install without the acceptance says what is missing, puts the focus on it and asks the server for nothing.
    await user.click(screen.getByRole("button", { name: "Install Noust DNS" }));
    expect(await screen.findByText("Accept what it takes on to install it.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "I understand and want to install it" })).toHaveFocus();
    expect(backend.callsTo("POST /api/dns/noust/server/install")).toHaveLength(0);

    await user.click(screen.getByRole("checkbox", { name: "I understand and want to install it" }));
    expect(screen.queryByText("Accept what it takes on to install it.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Install Noust DNS" }));
    await waitFor(() => expect(queued).toBe(1));
    expect(backend.callsTo("POST /api/dns/noust/server/install")[0]?.body).toEqual({ epel: false });
    await expectNoAxeViolations(document.body);
  });

  it("asks before it enables EPEL, naming the packages, and installs again with the answer", { timeout: 20_000 }, async () => {
    const bodies: unknown[] = [];
    const { user } = noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, NOT_INSTALLED),
      "POST /api/dns/noust/server/install": (call) => {
        bodies.push(call.body);
        if ((call.body as { epel: boolean }).epel) return json(202, { job_id: "j2", status: "queued", message: "queued" });
        return json(409, { error: "confirmation_required", detail: "EPEL is needed", hint: null, fields: null, output: null, required: { epel: true, packages: ["knot", "knot-utils"] } });
      },
    });

    await user.click(await screen.findByRole("checkbox", { name: "I understand and want to install it" }));
    await user.click(screen.getByRole("button", { name: "Install Noust DNS" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Enable EPEL to install it?" });
    expect(within(dialog).getByText("knot, knot-utils")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Enable EPEL and install" }));
    await waitFor(() => expect(bodies).toEqual([{ epel: false }, { epel: true }]));
  });

  it("says a system it cannot run on in its own words", { timeout: 20_000 }, async () => {
    const { user } = noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, NOT_INSTALLED),
      "POST /api/dns/noust/server/install": () => problem(501, "unsupported_platform", "No package of Knot DNS for this system", { hint: "Use a supported release." }),
    });
    await user.click(await screen.findByRole("checkbox", { name: "I understand and want to install it" }));
    await user.click(screen.getByRole("button", { name: "Install Noust DNS" }));
    expect(await screen.findByText("This system cannot run Noust DNS")).toBeInTheDocument();
    expect(screen.getByText("No package of Knot DNS for this system")).toBeInTheDocument();
  });
});

describe("Noust DNS installed and not set up", () => {
  it("offers the addresses it found ticked and sets up a primary with the name it was given", { timeout: 20_000 }, async () => {
    const { user, backend } = noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, NOT_SET_UP),
      "POST /api/dns/noust/server": () => json(202, { job_id: "j3", status: "queued", message: "queued" }),
    });

    expect(await screen.findByText("Set up this server")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "203.0.113.10" })).toBeChecked();
    // Setting up without a name says what is missing where it is missing.
    await user.click(screen.getByRole("button", { name: "Set up" }));
    expect(await screen.findByText("Fill this in.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/dns/noust/server")).toHaveLength(0);

    await user.type(screen.getByLabelText("Name server name"), "NS1.example.net");
    await user.click(screen.getByRole("button", { name: "Set up" }));
    await waitFor(() => expect(backend.callsTo("POST /api/dns/noust/server")).toHaveLength(1));
    expect(backend.callsTo("POST /api/dns/noust/server")[0]?.body).toEqual({ role: "primary", epel: false, hostname: "ns1.example.net", listen: ["203.0.113.10"] });
  });

  it("asks a secondary for its primary and the key", { timeout: 20_000 }, async () => {
    const { user, backend } = noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, NOT_SET_UP),
      "POST /api/dns/noust/server": () => json(202, { job_id: "j4", status: "queued", message: "queued" }),
    });
    await user.click(await screen.findByRole("radio", { name: /Secondary/ }));
    await user.type(screen.getByLabelText("Name server name"), "ns2.example.net");
    await user.type(screen.getByLabelText("Primary's address"), "203.0.113.10");
    await user.type(screen.getByLabelText("Transfer key"), "c2VjcmV0");
    await user.click(screen.getByRole("button", { name: "Set up" }));
    await waitFor(() => expect(backend.callsTo("POST /api/dns/noust/server")).toHaveLength(1));
    expect(backend.callsTo("POST /api/dns/noust/server")[0]?.body).toEqual({
      role: "secondary",
      epel: false,
      hostname: "ns2.example.net",
      listen: ["203.0.113.10"],
      primary_address: "203.0.113.10",
      transfer_key: { secret: "c2VjcmV0" },
    });
  });
});

describe("Noust DNS set up", () => {
  it("lists the zones and the secondaries of a primary, and opens a zone", { timeout: 20_000 }, async () => {
    const { user } = noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, PRIMARY),
      "GET /api/dns/noust/zones": () => json(200, ZONES),
      "GET /api/dns/noust/zones/example.com": () => json(200, ZONE),
    });
    const zones = await screen.findByRole("region", { name: "Zones of Noust DNS" });
    expect(await within(zones).findByText("example.com")).toBeInTheDocument();
    expect(await screen.findByText("ns2.example.net")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add zone" }).length).toBeGreaterThan(0);
    await expectNoAxeViolations(document.body);
    await user.click(within(zones).getByRole("button", { name: "example.com" }));
    expect(await screen.findByRole("heading", { level: 1, name: "example.com" })).toBeInTheDocument();
  });

  it("is read only on a secondary, which copies its zones from the primary", async () => {
    noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, SECONDARY),
      "GET /api/dns/noust/zones": () => json(200, { zones: [{ ...ZONES.zones[0], role: "slave", dnssec: null }] }),
    });
    expect(await screen.findByText("This server copies its zones from the primary. Edit them there.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add zone" })).not.toBeInTheDocument();
    expect(screen.queryByText("Secondaries")).not.toBeInTheDocument();
  });

  it("raises a banner when the name server is not running", async () => {
    noustConsole("/domains/dns/noust", {
      "GET /api/dns/noust/server": () => json(200, { ...PRIMARY, running: false }),
      "GET /api/dns/noust/zones": () => json(200, ZONES),
    });
    expect(await screen.findByText("Noust DNS is not running on this server")).toBeInTheDocument();
  });
});

describe("the DNS section's entry to Noust DNS", () => {
  it("says where the server stands and leads to its page", async () => {
    noustConsole("/domains/dns", { "GET /api/dns/noust/server": () => json(200, PRIMARY) });
    expect(await screen.findByText("Answer for your domains from your own servers, instead of a provider's.")).toBeInTheDocument();
    expect(await screen.findByText("Primary server")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Noust DNS" })).toHaveAttribute("href", "/domains/dns/noust");
  });

  it("is absent on a server whose Noust has no Noust DNS", async () => {
    noustConsole("/domains/dns", { "GET /api/dns/noust/server": () => problem(404, "not_found", "No such route") });
    expect(await screen.findByRole("heading", { level: 1, name: "DNS" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Answer for your domains from your own servers, instead of a provider's.")).not.toBeInTheDocument());
  });
});

describe("a zone", () => {
  const zoneRoutes = {
    "GET /api/dns/noust/server": () => json(200, PRIMARY),
    "GET /api/dns/noust/zones/example.com": () => json(200, ZONE),
  };

  it("shows its records and adds one, naming the field when the server refuses it", { timeout: 20_000 }, async () => {
    let attempts = 0;
    const { user, backend } = noustConsole("/domains/dns/zones/example.com", {
      ...zoneRoutes,
      "POST /api/dns/noust/zones/example.com/records": () => {
        attempts += 1;
        return attempts === 1 ? problem(400, "validation_error", "Invalid", { fields: { content: "Not an IPv4 address" } }) : json(201, { ...ZONE.records[0], id: 3, name: "api", content: "203.0.113.9" });
      },
    });

    const table = await screen.findByRole("region", { name: "Records of example.com" });
    expect(within(table).getByText("203.0.113.7")).toBeInTheDocument();
    expect(within(table).getByText("Zone's")).toBeInTheDocument();
    await user.click(first(screen.getAllByRole("button", { name: "Add record" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a record" });
    await user.type(within(dialog).getByLabelText(/^Name/), "api");
    await user.type(within(dialog).getByLabelText("Value"), "nonsense");
    await user.click(within(dialog).getByRole("button", { name: "Add record" }));
    expect(await within(dialog).findByText("Not an IPv4 address")).toBeInTheDocument();

    await user.clear(within(dialog).getByLabelText("Value"));
    await user.type(within(dialog).getByLabelText("Value"), "203.0.113.9");
    await user.click(within(dialog).getByRole("button", { name: "Add record" }));
    await waitFor(() => expect(backend.callsTo("POST /api/dns/noust/zones/example.com/records")).toHaveLength(2));
    expect(backend.callsTo("POST /api/dns/noust/zones/example.com/records")[1]?.body).toEqual({ name: "api", type: "A", content: "203.0.113.9" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Add a record" })).not.toBeInTheDocument());
  });

  it("shows the name server's own words, verbatim, when it rejects a change", { timeout: 20_000 }, async () => {
    const { user } = noustConsole("/domains/dns/zones/example.com", {
      ...zoneRoutes,
      "POST /api/dns/noust/zones/example.com/records": () => problem(502, "dns_server_error", "Knot refused the zone", { output: "error: [example.com.] zone check, line 7: unknown type" }),
    });
    await user.click(first(await screen.findAllByRole("button", { name: "Add record" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a record" });
    await user.type(within(dialog).getByLabelText("Value"), "203.0.113.9");
    await user.click(within(dialog).getByRole("button", { name: "Add record" }));
    expect(await within(dialog).findByText("The record was not added")).toBeInTheDocument();
    expect(within(dialog).getByText(/zone check, line 7: unknown type/)).toBeInTheDocument();
  });

  it("deletes a record after one question", { timeout: 20_000 }, async () => {
    const { user, backend } = noustConsole("/domains/dns/zones/example.com", {
      ...zoneRoutes,
      "DELETE /api/dns/noust/zones/example.com/records/2": () => json(200, { removed: true }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for www CNAME" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete record" }));
    await waitFor(() => expect(backend.callsTo("DELETE /api/dns/noust/zones/example.com/records/2")).toHaveLength(1));
  });

  it("is read only on a server that copies it", async () => {
    noustConsole("/domains/dns/zones/example.com", { ...zoneRoutes, "GET /api/dns/noust/server": () => json(200, SECONDARY) });
    expect(await screen.findByText("This server copies this zone. Edit it on the primary.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add record" })).not.toBeInTheDocument();
  });

  it("turns DNSSEC on and, once there is a DS, shows it to copy", { timeout: 20_000 }, async () => {
    let enabled = false;
    const { user, backend } = noustConsole("/domains/dns/zones/example.com?view=dnssec", {
      ...zoneRoutes,
      "GET /api/dns/noust/zones/example.com/dnssec": () => json(200, { zone: "example.com", enabled, algorithm: "ecdsap256sha256", ds: enabled ? [DS] : [] }),
      "PUT /api/dns/noust/zones/example.com/dnssec": () => {
        enabled = true;
        return json(200, { zone: "example.com", enabled: true, algorithm: "ecdsap256sha256", ds: [DS] });
      },
      "POST /api/dns/noust/zones/example.com/dnssec/ds-published": () => json(200, { zone: "example.com", enabled: true, algorithm: "ecdsap256sha256", ds: [DS] }),
    });
    await user.click(await screen.findByRole("button", { name: "Turn on DNSSEC" }));
    expect(backend.callsTo("PUT /api/dns/noust/zones/example.com/dnssec")[0]?.body).toEqual({ enabled: true, ds_removed: false });
    expect(await screen.findByText("Give the registrar this DS record")).toBeInTheDocument();
    expect(screen.getByText("example.com. IN DS 12345 13 2 ABCDEF0123456789")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "I gave it to my registrar" }));
    await waitFor(() => expect(backend.callsTo("POST /api/dns/noust/zones/example.com/dnssec/ds-published")).toHaveLength(1));
  });

  it("does not turn DNSSEC off until the DS is gone from the registrar", { timeout: 20_000 }, async () => {
    const { user, backend } = noustConsole("/domains/dns/zones/example.com?view=dnssec", {
      ...zoneRoutes,
      "GET /api/dns/noust/zones/example.com/dnssec": () => json(200, { zone: "example.com", enabled: true, algorithm: "ecdsap256sha256", ds: [DS] }),
      "PUT /api/dns/noust/zones/example.com/dnssec": () => json(200, { zone: "example.com", enabled: false, algorithm: "ecdsap256sha256", ds: [] }),
    });
    await user.click(await screen.findByRole("button", { name: "Turn off DNSSEC" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Turn off DNSSEC" }));
    expect(await within(dialog).findAllByText("Remove the DS record at the registrar first, then say so here.")).not.toHaveLength(0);
    expect(backend.callsTo("PUT /api/dns/noust/zones/example.com/dnssec")).toHaveLength(0);

    await user.click(within(dialog).getByRole("checkbox", { name: "I removed the DS record at my registrar" }));
    await user.click(within(dialog).getByRole("button", { name: "Turn off DNSSEC" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/dns/noust/zones/example.com/dnssec")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/dns/noust/zones/example.com/dnssec")[0]?.body).toEqual({ enabled: false, ds_removed: true });
  });

  it("says the steps to delegate in the console's words and what the check found, with the fix", { timeout: 20_000 }, async () => {
    const { user } = noustConsole("/domains/dns/zones/example.com?view=delegation", {
      ...zoneRoutes,
      "GET /api/dns/noust/zones/example.com/delegation": () =>
        json(200, {
          zone: "example.com",
          dnssec: false,
          ds: [],
          nameservers: [
            { name: "ns1.example.net", addresses: ["203.0.113.10"], glue: false },
            { name: "ns2.example.com", addresses: [], glue: true },
          ],
          steps: [{ code: "authdns.delegation.nameservers", params: { zone: "example.com", nameservers: "ns1.example.net, ns2.example.net" }, text: "English fallback", hint: "English hint" }],
          warnings: [{ code: "authdns.delegation.one_server", params: { zone: "example.com" }, text: "English warning", hint: null }],
        }),
      "GET /api/dns/noust/zones/example.com/delegation/check": () =>
        json(200, {
          zone: "example.com",
          delegated: false,
          public_nameservers: ["ns1.old.example"],
          ds_published: null,
          ds_matches: null,
          ready: false,
          servers: [{ name: "ns1.example.net", address: "203.0.113.10", answers: true, authoritative: true, serial: 2026102901, refuses_recursion: true }],
          problems: [{ code: "authdns.check.not_delegated", params: { zone: "example.com", found: "ns1.old.example" }, text: "English problem", hint: "English fix" }],
        }),
    });

    expect(await screen.findByText("At your registrar, set the name servers of example.com to ns1.example.net, ns2.example.net")).toBeInTheDocument();
    expect(screen.queryByText("English fallback")).not.toBeInTheDocument();
    expect(screen.getByText("Needs an address at the registrar")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Resolvers find ns1.old.example for example.com, not your name servers")).toBeInTheDocument();
    expect(screen.getByText("Change the name servers at your registrar and wait for its TTL to pass.")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });
});

describe("Noust DNS on several servers, from the central", () => {
  it("sends a primary and its secondary to the central, which sets them up as one fleet job", { timeout: 30_000 }, async () => {
    const { user, backend, location } = noustConsole("/domains/dns", {
      "GET /api/nodes": () => json(200, { items: NODES }),
      "GET /api/dns/noust/server": () => json(200, NOT_INSTALLED),
      "POST /api/fleet/dns/cluster": () => json(200, { plan: { action: "dns_cluster", nodes: [], batches: [] }, job: { job_id: "f1", status: "queued", message: "queued" } }),
    });

    await user.click(await screen.findByRole("button", { name: "Set up on several servers" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up Noust DNS on several servers" });
    // Without the servers and their names it says what is missing and asks the central for nothing.
    await user.click(within(dialog).getByRole("button", { name: "Set up" }));
    expect(await within(dialog).findByText("Choose a server and give a name for each.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/dns/cluster")).toHaveLength(0);

    await user.click(within(dialog).getByRole("combobox", { name: "Server" }));
    await user.click(await screen.findByRole("option", { name: "web-2" }));
    await user.type(within(dialog).getByLabelText("Name server name"), "ns1.example.net");
    await user.click(within(dialog).getByRole("combobox", { name: "Server of secondary 1" }));
    await user.click(await screen.findByRole("option", { name: "db-1" }));
    await user.type(within(dialog).getByLabelText("Name of secondary 1"), "ns2.example.net");
    await user.click(within(dialog).getByRole("button", { name: "Set up" }));

    await waitFor(() => expect(backend.callsTo("POST /api/fleet/dns/cluster")).toHaveLength(1));
    expect(backend.callsTo("POST /api/fleet/dns/cluster")[0]?.body).toEqual({
      primary: { node: "web-2", hostname: "ns1.example.net" },
      secondaries: [{ node: "db-1", hostname: "ns2.example.net" }],
      epel: false,
      plan: false,
    });
    await waitFor(() => expect(location().pathname).toBe("/fleet/jobs/f1"));
  });

  it("is not offered on a server that is no central", async () => {
    noustConsole("/domains/dns", { "GET /api/dns/noust/server": () => json(200, NOT_INSTALLED) });
    expect(await screen.findByRole("link", { name: "Set up Noust DNS" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set up on several servers" })).not.toBeInTheDocument();
  });
});
