import { describe, expect, it } from "vitest";

import openapiText from "../../../openapi.json?raw";
import { NOT_OFFERED_BY_A_DIALOG, PLANNED_OPERATIONS, planPath } from "./planOperations";

interface Operation {
  "x-noust-plan"?: { method: string; path: string; operation: string };
}

const openapi = JSON.parse(openapiText) as { paths: Record<string, Record<string, Operation>> };

/** Every operation the API says has a simulation, as "METHOD /path": the truth the console's table is held to. */
const MARKED: ReadonlyMap<string, { method: string; path: string }> = new Map(
  Object.entries(openapi.paths).flatMap(([path, methods]) =>
    Object.entries(methods).flatMap(([method, operation]) => (operation["x-noust-plan"] !== undefined ? [[`${method.toUpperCase()} ${path}`, operation["x-noust-plan"]] as const] : [])),
  ),
);

describe("the operations that have 'See what it will do'", () => {
  it("are the ones the API marks, each with the plan route the API names", () => {
    for (const [name, planned] of Object.entries(PLANNED_OPERATIONS)) {
      const marked = MARKED.get(`${planned.method} ${planned.path}`);
      expect(marked, `${name}: the API does not mark ${planned.method} ${planned.path} as having a plan`).toBeDefined();
      expect(marked?.path, `${name}: another plan route`).toBe(planned.plan);
      expect(marked?.method).toBe("POST");
    }
  });

  it("leave out none that the API marks without saying why", () => {
    const known = new Set([...Object.values(PLANNED_OPERATIONS).map((planned) => `${planned.method} ${planned.path}`), ...Object.keys(NOT_OFFERED_BY_A_DIALOG)]);
    const missing = [...MARKED.keys()].filter((key) => !known.has(key));
    expect(missing, "an operation the API says has a plan that no dialog of the console offers and the list does not explain").toEqual([]);
  });

  it("fill the values of an operation's path into the plan route, encoded", () => {
    expect(planPath("update", { domain: "shop.example.com" })).toBe("/api/apps/shop.example.com/update/plan");
    expect(planPath("rollback", { domain: "shop.example.com", deployment_id: 14 })).toBe("/api/apps/shop.example.com/deployments/14/rollback/plan");
    expect(planPath("removeDomain", { domain: "a.example.com", name: "www b" })).toBe("/api/apps/a.example.com/domains/www%20b/plan");
  });
});
