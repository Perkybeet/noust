import { describe, expect, it } from "vitest";

import { ACCOUNT_NAME, EMPTY_CREATE, SCRIPT_NAME, createBody, createProblem, guessKind, imagesFor, machineStatus, plansIn, plausibleToken, preferredImage, priceOf } from "./data";
import type { CloudCatalog, CloudChoice } from "./data";

const choice = (patch: Partial<CloudChoice> & { id: string }): CloudChoice => ({ name: patch.id, detail: "", ...patch });

const CATALOG: CloudCatalog = {
  provider: "hetzner",
  capabilities: { ipv6: true, backups: true, firewalls: true, networks: true },
  locations: [choice({ id: "fsn1", name: "Falkenstein" }), choice({ id: "ash", name: "Ashburn" })],
  plans: [
    choice({ id: "cx32", name: "CX32", locations: ["fsn1", "ash"], arch: "x86", monthly: 6.8 }),
    choice({ id: "cx22", name: "CX22", locations: ["fsn1"], arch: "x86", monthly: 3.8 }),
    choice({ id: "cax11", name: "CAX11", locations: ["fsn1"], arch: "arm", monthly: 3.3 }),
    choice({ id: "free", name: "Free", locations: [] }),
  ],
  images: [
    choice({ id: "ubuntu-22.04", name: "Ubuntu 22.04", archs: ["x86", "arm"] }),
    choice({ id: "ubuntu-24.04", name: "Ubuntu 24.04", archs: ["x86", "arm"] }),
    choice({ id: "debian-12", name: "Debian 12", archs: ["x86"] }),
    choice({ id: "alma-9", name: "AlmaLinux 9" }),
  ],
  ssh_keys: [],
  firewalls: [],
  networks: [],
};

describe("what a form for a cloud server accepts", () => {
  it("names the first thing wrong, in the order the steps ask", () => {
    expect(createProblem(EMPTY_CREATE)).toBe("name");
    expect(createProblem({ ...EMPTY_CREATE, name: "Web 2" })).toBe("name");
    expect(createProblem({ ...EMPTY_CREATE, name: "web-2" })).toBe("account");
    const named = { ...EMPTY_CREATE, name: "web-2", account: "main" };
    expect(createProblem(named)).toBe("location");
    expect(createProblem({ ...named, location: "fsn1" })).toBe("plan");
    expect(createProblem({ ...named, location: "fsn1", plan: "cx22" })).toBe("image");
    const whole = { ...named, location: "fsn1", plan: "cx22", image: "ubuntu-24.04" };
    expect(createProblem(whole)).toBeNull();
    // The central reaches the server over SSH, so it needs an address.
    expect(createProblem({ ...whole, ipv4: false, ipv6: false })).toBe("addresses");
  });

  it("accepts the names the API accepts", () => {
    expect(ACCOUNT_NAME.test("hetzner-prod")).toBe(true);
    expect(ACCOUNT_NAME.test("Hetzner")).toBe(false);
    expect(ACCOUNT_NAME.test("-x")).toBe(false);
    expect(SCRIPT_NAME.test("swap-4g")).toBe(true);
    expect(SCRIPT_NAME.test("base.packages_2")).toBe(true);
    expect(SCRIPT_NAME.test("../etc")).toBe(false);
  });

  it("takes a pasted token only if it could be one", () => {
    expect(plausibleToken("x".repeat(64))).toBe(true);
    expect(plausibleToken(`  ${"x".repeat(40)}\n`)).toBe(true);
    expect(plausibleToken("short")).toBe(false);
    expect(plausibleToken("two words in a sentence that is long enough")).toBe(false);
    expect(plausibleToken("x".repeat(600))).toBe(false);
  });

  it("tells a boot script from a cloud-config fragment by its first line", () => {
    expect(guessKind("#cloud-config\npackages: [htop]")).toBe("cloud-config");
    expect(guessKind("#!/bin/sh\necho hi")).toBe("script");
  });
});

describe("what a provider sells where", () => {
  it("lists the plans of a location, the cheapest first, and the ones with no price last", () => {
    expect(plansIn(CATALOG, "fsn1").map((plan) => plan.id)).toEqual(["cax11", "cx22", "cx32", "free"]);
    // A plan with no list of locations is sold everywhere; one listed elsewhere is not.
    expect(plansIn(CATALOG, "ash").map((plan) => plan.id)).toEqual(["cx32", "free"]);
  });

  it("offers only the images that run on the plan's processor", () => {
    const arm = CATALOG.plans.find((plan) => plan.id === "cax11");
    expect(imagesFor(CATALOG, arm).map((image) => image.id)).toEqual(["alma-9", "ubuntu-22.04", "ubuntu-24.04"]);
    const x86 = CATALOG.plans.find((plan) => plan.id === "cx22");
    expect(imagesFor(CATALOG, x86).map((image) => image.id)).toEqual(["alma-9", "debian-12", "ubuntu-22.04", "ubuntu-24.04"]);
    // With no plan chosen yet, nothing is ruled out.
    expect(imagesFor(CATALOG, undefined)).toHaveLength(4);
  });

  it("starts from the newest supported release the list has, and from nothing when it has none", () => {
    expect(preferredImage(CATALOG.images)).toBe("ubuntu-24.04");
    expect(preferredImage([choice({ id: "debian-12", name: "Debian 12" }), choice({ id: "ubuntu-22.04", name: "Ubuntu 22.04" })])).toBe("debian-12");
    expect(preferredImage([choice({ id: "alma-9", name: "AlmaLinux 9" })])).toBe("");
  });

  it("quotes a price only when the provider does", () => {
    expect(priceOf(choice({ id: "a", monthly: 3.8 }))).toMatch(/3[.,]80/);
    expect(priceOf(choice({ id: "b" }))).toBeNull();
    expect(priceOf(undefined)).toBeNull();
  });
});

describe("the request a form becomes", () => {
  const form = { ...EMPTY_CREATE, name: " web-2 ", account: "main", location: "fsn1", plan: "cx22", image: "ubuntu-24.04", backups: true, ipv6: true, firewalls: ["f1"], scripts: ["swap-2g"] };

  it("sends what the provider can do and drops what it cannot", () => {
    const body = createBody(form, { ipv6: true, backups: true, firewalls: true });
    expect(body).toMatchObject({ name: "web-2", backups: true, ipv6: true, firewalls: ["f1"], networks: [], scripts: ["swap-2g"], allow_host_access: false });
    const bare = createBody(form, {});
    expect(bare).toMatchObject({ backups: false, ipv6: false, firewalls: [], networks: [] });
  });

  it("never carries a credential: there is none in the form", () => {
    expect(Object.keys(createBody(form, {})).join(" ")).not.toMatch(/token|password|secret/i);
  });
});

describe("a machine's state as a status", () => {
  it("tells it three ways: a machine on its way is working, one that did not make it failed, one gone stopped", () => {
    expect(machineStatus("ready")).toBe("running");
    expect(machineStatus("creating")).toBe("deploying");
    expect(machineStatus("booting")).toBe("deploying");
    expect(machineStatus("enrolling")).toBe("deploying");
    expect(machineStatus("failed")).toBe("failed");
    expect(machineStatus("deleted")).toBe("stopped");
    expect(machineStatus("something-new")).toBe("unknown");
  });
});
