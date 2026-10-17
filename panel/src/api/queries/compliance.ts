/**
 * Choosing compliance frameworks and controls (`/api/compliance`): which frameworks protect the
 * server, what each control is and which framework forces it, and the changes. Every rule - what a
 * framework turns on, what cannot be loosened, what needs a second look - is the server's; the
 * console asks, shows the answer and, when the server refuses, shows the refusal as it was worded.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type ComplianceState = ResponseOf<"/api/compliance", "get">;
export type ComplianceFramework = ComplianceState["frameworks"][number];
export type ComplianceControl = ComplianceState["controls"][number];
export type CompliancePlan = ResponseOf<"/api/compliance/plan", "post">;
export type ComplianceBody = BodyOf<"/api/compliance", "put">;
/** What a caller asks for: the frameworks to turn on or off and the controls to set. */
export type ComplianceChange = Omit<ComplianceBody, "acknowledge"> & { acknowledge?: boolean };
export type ComplianceResult = ResponseOf<"/api/compliance", "put">;

export const complianceKeys = {
  all: ["compliance"] as const,
  state: ["compliance", "state"] as const,
};

/** The frameworks, the controls, and what enabling each framework would change. */
export const complianceQuery = () =>
  queryOptions({
    queryKey: complianceKeys.state,
    queryFn: ({ signal }) => request("get", "/api/compliance", { signal }),
  });

/** What a change would do, writing nothing: the controls that move, and what needs a second look. */
export function planCompliance(body: ComplianceChange) {
  return request("post", "/api/compliance/plan", { body: { ...body, acknowledge: false } });
}

/** Makes a change. Turning a framework off or loosening a control needs "Confirm it's you". */
export function changeCompliance(body: ComplianceChange) {
  return request("put", "/api/compliance", { body: { ...body, acknowledge: body.acknowledge ?? false } });
}
