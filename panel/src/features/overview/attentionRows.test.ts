import { describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../../i18n";
import { itemTitle, originLabel, reasonText } from "./attentionWords";

const en = bindT("en");

function reason(kind: string, code: string, params: Record<string, unknown> = {}) {
  return { kind, origin: "state", severity: "warn", code, params, detail: null, when: null, deployment_id: null, actions: [] };
}

const machine = (code: string, params: Record<string, unknown> = {}) => reason("machine", code, params);

describe("the machine's facts and the findings of Security", () => {
  it("says each fact of the machine, in the same plain sentences as every other reason of the list", () => {
    expect(reasonText(en, machine("security_updates", { count: 4 }))).toBe("4 security updates are pending");
    expect(reasonText(en, machine("security_updates", { count: 1 }))).toBe("1 security update is pending");
    expect(reasonText(en, machine("packages_broken"))).toBe("The package database is half configured: an update was interrupted");
    expect(reasonText(en, machine("reboot_required", { packages: [] }))).toBe("A reboot is needed to finish updates");
    expect(reasonText(en, machine("reboot_required", { packages: ["linux-image-6.8", "libc6", "openssl", "systemd"] }))).toBe("A reboot is needed to finish updating linux-image-6.8, libc6, openssl");
    expect(reasonText(en, machine("os_unsupported", { name: "Ubuntu 20.04", date: null }))).toBe("Ubuntu 20.04 is out of support: it no longer gets security updates");
    expect(reasonText(en, machine("os_ending", { name: "Ubuntu 22.04", days: 40 }))).toBe("Ubuntu 22.04 goes out of support in 40 days");
    expect(reasonText(en, machine("disk_full", { mount: "/var", percent: 96, free: null }))).toBe("/var is 96% full");
    expect(reasonText(en, machine("clock_unsynced"))).toBe("The clock is not synchronised");
    expect(reasonText(en, machine("no_swap"))).toBe("Little memory and no swap: a build can be killed for lack of memory");
    expect(reasonText(en, machine("failed_units", { count: 2, units: ["fail2ban.service", "cron.service"] }))).toBe("2 system services failed: fail2ban.service, cron.service");
  });

  it("says a finding of the checks by how serious it is, and leaves what it found to the system's words under it", () => {
    const finding = (severity: string) => reason("security", "security_finding", { check: "fw.docker_bypass", group: "firewall", severity });
    expect(reasonText(en, finding("critical"))).toBe("A critical finding of the security checks");
    expect(reasonText(en, finding("warning"))).toBe("A finding of the security checks");
  });

  it("says it in Spanish", async () => {
    await loadCatalog("es");
    const es = bindT("es");
    expect(reasonText(es, machine("security_updates", { count: 4 }))).toBe("Hay 4 actualizaciones de seguridad pendientes");
    expect(reasonText(es, machine("clock_unsynced"))).toBe("El reloj no está sincronizado");
  });
});

describe("itemTitle", () => {
  const row = (subject: Record<string, unknown>, title: string) => ({ id: "x", subject, title, severity: "warn", reasons: [] });

  it("names a fact of the machine by what it is, in the language of the console", async () => {
    expect(itemTitle(en, row({ kind: "machine", fact: "disk" }, "Disk"))).toEqual({ text: "Disk", mono: false, translate: true });
    expect(itemTitle(en, row({ kind: "machine", fact: "security_updates" }, "Security updates")).text).toBe("Security updates");
    await loadCatalog("es");
    expect(itemTitle(bindT("es"), row({ kind: "machine", fact: "disk" }, "Disk")).text).toBe("Disco");
  });

  it("names a finding by its check as the Security page does, or by the server's own words for a check this console does not know", () => {
    expect(itemTitle(en, row({ kind: "finding", check: "fw.docker_bypass", group: "firewall" }, "Docker publishes ports around the firewall")).text).toBe("Docker publishes ports around the firewall");
    expect(itemTitle(en, row({ kind: "finding", check: "x.new_one", group: "x" }, "A check that is new")).text).toBe("A check that is new");
  });

  it("keeps a domain, a unit, a rule or a recipe as the system's own name", () => {
    expect(itemTitle(en, row({ kind: "app", domain: "shop.example.com" }, "shop.example.com"))).toEqual({ text: "shop.example.com", mono: false, translate: false });
    expect(itemTitle(en, row({ kind: "unit", name: "noust-web" }, "noust-web"))).toEqual({ text: "noust-web", mono: true, translate: false });
    expect(itemTitle(en, row({ kind: "alert", rule: "cpu" }, "cpu")).mono).toBe(true);
  });
});

describe("originLabel", () => {
  it("says where a row comes from with the name of the area that deals with it, and a state when the server did not say", async () => {
    expect(originLabel(en, "alert")).toBe("Alert");
    expect(originLabel(en, "security")).toBe("Security");
    expect(originLabel(en, "state")).toBe("State");
    // A server before 4.3 sends none.
    expect(originLabel(en, undefined)).toBe("State");
    await loadCatalog("es");
    expect(originLabel(bindT("es"), "alert")).toBe("Alerta");
    expect(originLabel(bindT("es"), "security")).toBe("Seguridad");
    expect(originLabel(bindT("es"), "state")).toBe("Estado");
  });
});
