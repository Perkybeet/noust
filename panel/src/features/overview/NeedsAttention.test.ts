import { describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../../i18n";
import { reasonText } from "./NeedsAttention";

const en = bindT("en");

function monitor(severity: string, signal: string) {
  return { kind: "monitor", severity: "warn", code: "monitor_finding", params: { severity, signal }, detail: null, when: null, deployment_id: null, actions: [] };
}

describe("reasonText", () => {
  it("says what the monitor saw in words, by its signal and severity, never its raw names", () => {
    expect(reasonText(en, monitor("notice", "name-pattern"))).toBe("Its name matches a tool worth a look");
    expect(reasonText(en, monitor("warning", "name-pattern"))).toBe("Its name matches a tool that needs a look");
    expect(reasonText(en, monitor("notice", "resource-usage"))).toBe("It uses more CPU or memory than usual");
    expect(reasonText(en, monitor("warning", "resource-usage"))).toBe("It has used too much CPU or memory for a while");
    // A signal this console does not know is still said, with its name as a value.
    expect(reasonText(en, monitor("warning", "open-port"))).toBe("The monitor flagged this process (open-port)");
  });

  it("calls a failed unit a service", () => {
    expect(reasonText(en, { ...monitor("", ""), kind: "unit", code: "unit_failed", params: {} })).toBe("The service has failed");
  });

  it("says it in Spanish", async () => {
    await loadCatalog("es");
    const es = bindT("es");
    expect(reasonText(es, monitor("warning", "resource-usage"))).toBe("Lleva un rato usando demasiada CPU o memoria");
    expect(reasonText(es, { ...monitor("", ""), kind: "unit", code: "unit_failed", params: {} })).toBe("El servicio ha fallado");
  });
});
