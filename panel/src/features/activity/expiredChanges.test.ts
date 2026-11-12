import { describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../../i18n";
import { auditActionLabel } from "./data";

describe("a change to how the server is reached that nobody confirmed", () => {
  it("is not called the same as the change itself (item 138)", () => {
    const en = bindT("en");

    expect(auditActionLabel(en, "server.firewall")).toBe("Firewall changed");
    expect(auditActionLabel(en, "server.firewall_expired")).toBe("The firewall change was undone because nobody confirmed it");
    expect(auditActionLabel(en, "server.ssh_expired")).toBe("The SSH change was undone because nobody confirmed it");
  });

  it("says it in Spanish", async () => {
    await loadCatalog("es");
    const es = bindT("es");

    expect(auditActionLabel(es, "server.firewall_expired")).toBe("El cambio del cortafuegos se deshizo porque nadie lo confirmó");
    expect(auditActionLabel(es, "server.ssh_expired")).toBe("El cambio de SSH se deshizo porque nadie lo confirmó");
  });
});
