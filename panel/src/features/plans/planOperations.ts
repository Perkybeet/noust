/**
 * Which operations have "See what it will do" (item 183), by the name the console gives them, and the plan route of each. The
 * truth is the API's description: an operation that has a simulation carries `x-noust-plan` in `panel/openapi.json`, and a test
 * (`planOperations.test.ts`) holds this table to it both ways: every entry here is marked there with the same route, and every
 * operation marked there is here or in the list of those no dialog offers yet, with why. So a dialog never offers a plan the API
 * does not have, and a new plan the API grows does not go unnoticed.
 */

export interface PlannedOperation {
  /** The operation the dialog applies, as the API names it. */
  method: "POST" | "PATCH" | "DELETE";
  path: string;
  /** The simulation of it: always a read-only POST, with the permission to read. */
  plan: string;
}

export const PLANNED_OPERATIONS = {
  update: { method: "POST", path: "/api/apps/{domain}/deploy", plan: "/api/apps/{domain}/update/plan" },
  rollback: { method: "POST", path: "/api/apps/{domain}/deployments/{deployment_id}/rollback", plan: "/api/apps/{domain}/deployments/{deployment_id}/rollback/plan" },
  restart: { method: "POST", path: "/api/apps/{domain}/restart", plan: "/api/apps/{domain}/restart/plan" },
  stop: { method: "POST", path: "/api/apps/{domain}/stop", plan: "/api/apps/{domain}/stop/plan" },
  start: { method: "POST", path: "/api/apps/{domain}/start", plan: "/api/apps/{domain}/start/plan" },
  limits: { method: "PATCH", path: "/api/apps/{domain}/limits", plan: "/api/apps/{domain}/limits/plan" },
  renewCertificate: { method: "POST", path: "/api/certs/{domain}/renew", plan: "/api/certs/{domain}/renew/plan" },
  addDomain: { method: "POST", path: "/api/apps/{domain}/domains", plan: "/api/apps/{domain}/domains/plan" },
  removeDomain: { method: "DELETE", path: "/api/apps/{domain}/domains/{name}", plan: "/api/apps/{domain}/domains/{name}/plan" },
  deleteApp: { method: "DELETE", path: "/api/apps/{domain}", plan: "/api/apps/{domain}/delete/plan" },
  migrate: { method: "POST", path: "/api/apps/{domain}/migrate", plan: "/api/apps/{domain}/migrate/plan" },
  dockerCleanup: { method: "POST", path: "/api/docker/cleanup", plan: "/api/docker/cleanup/plan" },
} as const satisfies Record<string, PlannedOperation>;

export type PlannedName = keyof typeof PLANNED_OPERATIONS;

/**
 * The operations that carry a plan in the API and that no dialog of the console applies through that route yet, with why. A job
 * that applies the same operation (`POST /api/jobs/update`, `POST /api/jobs/delete`) shares the plan of the one above it.
 */
export const NOT_OFFERED_BY_A_DIALOG: Readonly<Record<string, string>> = {
  "POST /api/jobs/update": "the console updates through `POST /api/apps/{domain}/deploy`; the job form shares its plan",
  "POST /api/jobs/delete": "the console deletes through `DELETE /api/apps/{domain}`; the job form shares its plan",
};

/** The plan route with the values of the operation's path put in. */
export function planPath(name: PlannedName, params: Readonly<Record<string, string | number>>): string {
  return PLANNED_OPERATIONS[name].plan.replace(/\{(\w+)\}/g, (_match, key: string) => encodeURIComponent(String(params[key] ?? "")));
}
