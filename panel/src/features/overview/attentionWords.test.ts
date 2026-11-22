import { describe, expect, it } from "vitest";

import { bindT, loadCatalog } from "../../i18n";
import { reasonDetail, reasonText } from "./attentionWords";

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

  it("says a first deploy that failed left no application, in both languages", async () => {
    const first = { ...monitor("", ""), kind: "first_deploy", severity: "fail", code: "first_deploy_failed", params: {} };
    expect(reasonText(en, first)).toBe("The first deploy failed, so no application was created");
    await loadCatalog("es");
    expect(reasonText(bindT("es"), first)).toBe("El primer despliegue falló y no se creó la aplicación");
  });

  it("calls a failed unit a service", () => {
    expect(reasonText(en, { ...monitor("", ""), kind: "unit", code: "unit_failed", params: {} })).toBe("The service has failed");
  });

  it("says a stack running outside its unit is not supervised, in both languages", async () => {
    const outside = { ...monitor("", ""), kind: "state", code: "running_outside_unit", params: {} };
    expect(reasonText(en, outside)).toBe("Its containers run while its unit is stopped: Noust is not supervising it");
    await loadCatalog("es");
    expect(reasonText(bindT("es"), outside)).toBe("Sus contenedores están en marcha con la unidad parada: Noust no la supervisa");
  });

  it("says a stack that has waited a week for the check of its start in days, in both languages", async () => {
    const waiting = { ...monitor("", ""), kind: "state", code: "compose_check_waiting", params: { days: 9 }, detail: "Docker Compose could not resolve the stack" };
    expect(reasonText(en, waiting)).toBe("This stack has waited 9 days to be brought under the check of its start");
    expect(reasonText(en, { ...waiting, params: { days: 1 } })).toBe("This stack has waited 1 day to be brought under the check of its start");
    expect(reasonDetail(en, waiting)).toEqual({ text: "Docker Compose could not resolve the stack", sentence: false });
    await loadCatalog("es");
    expect(reasonText(bindT("es"), waiting)).toBe("Esta pila lleva 9 días esperando la comprobación de su arranque");
  });

  // The container of an application that keeps falling asks for attention where the operator looks first (Home, the bell): the failure of
  // the application, and under it what the container did, as a sentence of Noust's in the console's language, not a program's words.
  it("says the cause of a container that keeps falling as a sentence of Noust's, in both languages", async () => {
    const loop = {
      kind: "state",
      severity: "fail",
      code: "service_failed",
      params: {},
      detail: "Out of memory (limit 256 MB): its container acme-web-1 was restarted 6 times in the last 10 minutes",
      detail_coded: { code: "attention.container_loop_oom_limit", params: { container: "acme-web-1", restarts: 6, minutes: 10, limit_mb: 256 } },
      when: null,
      deployment_id: null,
      actions: ["view_log", "diagnose"],
    };
    expect(reasonText(en, loop)).toBe("The service has failed");
    expect(reasonDetail(en, loop)).toEqual({ text: "Out of memory (limit 256 MB): its container acme-web-1 was restarted 6 times in the last 10 minutes", sentence: true });
    await loadCatalog("es");
    expect(reasonDetail(bindT("es"), loop)).toEqual({
      text: "Se queda sin memoria (límite de 256 MB): su contenedor acme-web-1 se ha reiniciado 6 veces en los últimos 10 minutos",
      sentence: true,
    });
  });

  it("says it in Spanish", async () => {
    await loadCatalog("es");
    const es = bindT("es");
    expect(reasonText(es, monitor("warning", "resource-usage"))).toBe("Lleva un rato usando demasiada CPU o memoria");
    expect(reasonText(es, { ...monitor("", ""), kind: "unit", code: "unit_failed", params: {} })).toBe("El servicio ha fallado");
  });
});
