import { describe, expect, it } from "vitest";

import { behind, canMove, display, groupFindings, hasSomethingToMove, movingFrom, needsAcceptance, orderSites, otherThan, planNumbers } from "./data";
import type { Finding, MovePlan, SitePlan, WebserverStatus } from "./queries";

const finding = (over: Partial<Finding>): Finding => ({
  reason: "unsupported",
  severity: "notes",
  directive: "gzip_vary",
  line: 4,
  protection: false,
  code: "webserver_move.approximate",
  params: {},
  text: "a note",
  ...over,
});

const site = (over: Partial<SitePlan>): SitePlan => ({
  domain: "shop.example.net",
  kind: "app",
  source: "nginx",
  target: "apache",
  source_path: "/etc/nginx/sites-available/shop.example.net",
  target_path: "/etc/apache2/sites-available/shop.example.net.conf",
  operator_site: false,
  keeps_operator_site: false,
  movable: true,
  ...over,
});

const plan = (over: Partial<MovePlan>): MovePlan => ({
  source: "nginx",
  target: "apache",
  mode: "handover",
  source_running: true,
  target_installed: true,
  target_running: false,
  install_command: "",
  sites: [site({})],
  blocked: false,
  losses: 0,
  confirmation: { code: "webserver_move.confirm_handover", params: {}, text: "Apache will take over ports 80 and 443 from nginx for 1 site(s)" },
  ...over,
});

const status = (over: Partial<WebserverStatus>): WebserverStatus => ({
  webserver: "nginx",
  holds_ports: "nginx",
  servers: {
    nginx: { installed: true, running: true, starts_at_boot: true, applications: 9, sites: 1, version: "1.24.0" },
    apache: { installed: false, running: false, starts_at_boot: false, applications: 0, sites: 0 },
  },
  ...over,
});

describe("which web server the sites move from and to", () => {
  it("moves from the one that holds ports 80 and 443, and to the other", () => {
    expect(movingFrom(status({}))).toBe("nginx");
    expect(otherThan("nginx")).toBe("apache");
    expect(otherThan("apache")).toBe("nginx");
  });

  it("moves from the one the setting names when neither (or both) holds the ports", () => {
    expect(movingFrom(status({ holds_ports: null, webserver: "apache" }))).toBe("apache");
  });

  it("counts applications and sites behind a server, and offers a move only when something is behind the serving one", () => {
    expect(behind(status({}), "nginx")).toBe(10);
    expect(behind(status({}), "apache")).toBe(0);
    expect(hasSomethingToMove(status({}))).toBe(true);
    expect(
      hasSomethingToMove(
        status({ servers: { nginx: { installed: true, running: true, starts_at_boot: true, applications: 0, sites: 0 }, apache: { installed: true, running: false, starts_at_boot: false, applications: 0, sites: 0 } } }),
      ),
    ).toBe(false);
  });

  it("writes the two names the way they are written", () => {
    expect(display("apache")).toBe("Apache");
    expect(display("nginx")).toBe("nginx");
  });
});

describe("what a site would lose", () => {
  it("reads what stops it first, then the protections, then what else is lost, then the notes", () => {
    const grouped = groupFindings({
      findings: [
        finding({ severity: "notes", directive: "gzip_vary" }),
        finding({ severity: "loses", protection: false, directive: "sub_filter" }),
        finding({ severity: "loses", protection: true, directive: "limit_req" }),
        finding({ severity: "blocks", directive: "include" }),
      ],
    });

    expect(grouped.blocks.map((entry) => entry.directive)).toEqual(["include"]);
    expect(grouped.protections.map((entry) => entry.directive)).toEqual(["limit_req"]);
    expect(grouped.loses.map((entry) => entry.directive)).toEqual(["sub_filter"]);
    expect(grouped.notes.map((entry) => entry.directive)).toEqual(["gzip_vary"]);
  });

  it("is empty for a site with no findings", () => {
    expect(groupFindings({})).toEqual({ blocks: [], protections: [], loses: [], notes: [] });
  });
});

describe("the order the sites are read in", () => {
  it("puts what stays first, then what loses a protection, then what loses something else, then the rest by name", () => {
    const ordered = orderSites([
      site({ domain: "z.example.net" }),
      site({ domain: "a.example.net", findings: [finding({ severity: "loses", protection: false })] }),
      site({ domain: "m.example.net", findings: [finding({ severity: "loses", protection: true })] }),
      site({ domain: "stuck.example.net", movable: false }),
      site({ domain: "b.example.net" }),
    ]);

    expect(ordered.map((entry) => entry.domain)).toEqual(["stuck.example.net", "m.example.net", "a.example.net", "b.example.net", "z.example.net"]);
  });

  it("does not change the plan it was given", () => {
    const sites = [site({ domain: "b.example.net" }), site({ domain: "a.example.net", movable: false })];
    orderSites(sites);

    expect(sites.map((entry) => entry.domain)).toEqual(["b.example.net", "a.example.net"]);
  });
});

describe("the plan in numbers", () => {
  it("counts the sites that move, the ones that stay and the protections lost", () => {
    const numbers = planNumbers(
      plan({
        losses: 2,
        sites: [site({}), site({ domain: "b.example.net", movable: false }), site({ domain: "c.example.net", findings: [finding({ severity: "loses", protection: true })] })],
      }),
    );

    expect(numbers).toEqual({ sites: 3, movable: 2, blocked: 1, losses: 2, protections: 1 });
  });

  it("asks the person to accept only when the new sites lose something", () => {
    expect(needsAcceptance(plan({ losses: 0 }))).toBe(false);
    expect(needsAcceptance(plan({ losses: 3 }))).toBe(true);
  });

  it("cannot be started when anything stops it, or there is nothing to move", () => {
    expect(canMove(plan({}))).toBe(true);
    expect(canMove(plan({ blocked: true }))).toBe(false);
    expect(canMove(plan({ sites: [] }))).toBe(false);
  });
});
