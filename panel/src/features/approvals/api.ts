/**
 * Four-eyes approvals (`/api/approvals`): the requests a second person decides, the policy
 * that says which calls need one, and deciding. Always the central's own: a node's call is
 * approved where the accounts live (api/nodeScope.ts).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";

export type ApprovalList = ResponseOf<"/api/approvals", "get">;
export type Approval = ApprovalList["approvals"][number];
export type ApprovalPolicy = ResponseOf<"/api/approvals/policy", "get">;
export type ApprovalState = "requested" | "approved" | "rejected" | "expired" | "executed";

export const approvalKeys = {
  all: ["approvals"] as const,
  list: (state: ApprovalState | null, mine: boolean) => ["approvals", "list", state, mine] as const,
  one: (id: string) => ["approvals", "one", id] as const,
  policy: ["approvals", "policy"] as const,
};

/** The requests the caller may see, newest first: every one for a decider, their own otherwise. */
export const approvalsQuery = (state: ApprovalState | null = null, mine = false) =>
  queryOptions({
    queryKey: approvalKeys.list(state, mine),
    queryFn: ({ signal }) =>
      request("get", "/api/approvals", { query: { ...(state !== null ? { state } : {}), ...(mine ? { mine } : {}) }, signal }),
  });

/** One request, watched while its requester waits. */
export const approvalQuery = (id: string) =>
  queryOptions({
    queryKey: approvalKeys.one(id),
    queryFn: ({ signal }) => request("get", "/api/approvals/{approval_id}", { params: { approval_id: id }, signal }),
  });

export const approvalPolicyQuery = () =>
  queryOptions({
    queryKey: approvalKeys.policy,
    queryFn: ({ signal }) => request("get", "/api/approvals/policy", { signal }),
    staleTime: 60_000,
  });

export function approve(id: number, comment: string | null) {
  return request("post", "/api/approvals/{approval_id}/approve", { params: { approval_id: id }, body: { comment } });
}

export function reject(id: number, comment: string | null) {
  return request("post", "/api/approvals/{approval_id}/reject", { params: { approval_id: id }, body: { comment } });
}
