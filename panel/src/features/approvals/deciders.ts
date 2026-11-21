/**
 * Who could decide an approval: how many people hold the role that does (Security officer by default), other than the one asking.
 *
 * With approvals on and nobody else holding the role, the first day is a dead end: an invitation waits for a person who does not
 * exist, and so does giving the role to someone, which is itself a change that waits for approval. The console says it before
 * that happens (when approvals are turned on) and where it is happening (the request that waits), from what the server already
 * gives: the roles that decide (`GET /api/approvals/policy`) and how many accounts hold each role (`GET /api/roles`, which takes the
 * permission to read accounts: for a person who has not got it nothing is known, and nothing is said).
 */

import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";
import { rolesQuery } from "../../api/queries/roles";
import { approvalPolicyQuery } from "./api";

export interface Deciders {
  /** The roles that decide, as the policy names them. */
  roles: readonly string[];
  /**
   * How many people hold one of them, not counting the person asking (nobody decides their own request); null while it is not known,
   * or for a person who may not read the accounts.
   */
  others: number | null;
  /** The person asking holds one of the roles: when nobody else does, they are alone with it. */
  you: boolean;
}

/** What the policy and the roles say of who could decide, for the person who is asking. */
export function useDeciders(): Deciders {
  // Read from the session itself and not through `useCan`: the dialog of a request is mounted above the router and is part of the first
  // script the console loads, and the piece that answers "may this person" brings the popovers and the buttons with it.
  const { data: mayRead } = useQuery({ ...sessionQuery(), select: (session) => session.permissions?.includes("accounts.read") ?? false });
  const policy = useQuery(approvalPolicyQuery());
  const roles = useQuery({ ...rolesQuery(), enabled: mayRead === true && policy.data !== undefined });
  const { data: mine } = useQuery({ ...sessionQuery(), select: (session) => session.roles ?? (session.role !== null && session.role !== undefined ? [session.role] : []) });
  const deciding = policy.data?.approvers ?? [];
  const you = (mine ?? []).some((role) => deciding.includes(role));
  if (policy.data === undefined || roles.data === undefined) return { roles: deciding, others: null, you };
  const holders = roles.data.roles.filter((role) => deciding.includes(role.name)).reduce((sum, role) => sum + role.holder_count, 0);
  return { roles: deciding, others: Math.max(0, holders - (you ? 1 : 0)), you };
}
