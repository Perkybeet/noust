/**
 * The policy of who calls (`/api/config/approval-policy`, item 184): what a token and the built-in assistant do with a call that
 * changes something, in production and in the other environments. Reading it takes the settings permission; changing it takes
 * the security one and sudo mode, and giving more freedom may take a second person. A server older than 4.3 answers 404 and the
 * page says it has no policy.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type PolicyTable = ResponseOf<"/api/config/approval-policy", "get">;
export type PolicyRow = PolicyTable["rows"][number];
export type PolicyCellInfo = PolicyRow["cells"][number];
export type PolicyChange = BodyOf<"/api/config/approval-policy", "put">;
export type PolicyPreview = ResponseOf<"/api/config/approval-policy/preview", "post">;

export const callerPolicyKeys = {
  table: ["approvals", "policy"] as const,
};

export const callerPolicyQuery = () =>
  queryOptions({
    queryKey: callerPolicyKeys.table,
    queryFn: ({ signal }) => request("get", "/api/config/approval-policy", { signal }),
    // A server that does not have it says so once; asking again every time the page is entered would only repeat the answer.
    retry: false,
  });

/** Saves the cells that changed and puts back the ones that were reset, all or nothing. Sudo mode, and a second person where it loosens. */
export function saveCallerPolicy(body: PolicyChange) {
  return request("put", "/api/config/approval-policy", { body });
}

/** What saving would do to the credentials that exist, without writing anything. */
export function previewCallerPolicy(body: PolicyChange) {
  return request("post", "/api/config/approval-policy/preview", { body });
}
