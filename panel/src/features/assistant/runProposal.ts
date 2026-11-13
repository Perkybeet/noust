/**
 * Carrying out a proposal: the browser makes the exact call the proposal holds, with the person's own session, and tells the server what
 * came of it. The server grants nothing new: sudo mode and the second person are asked as always, by the same client that asks them for
 * any other call of the console. The call carries the proposal's identifier as `X-Noust-Request-Id`, which is how the server proves from
 * the audit trail that the call it proposed is the one that was made, and the state the panel draws is the one the server answers with,
 * never what the browser saw.
 */

import { ApiError, ApprovalPendingError, ElevationCancelledError, api } from "../../api/client";
import type { Method } from "../../api/client";
import { reportOutcome } from "../../api/queries/assistantChat";
import type { OutcomeBody } from "../../api/queries/assistantChat";
import { asProposal } from "./model";
import type { Proposal } from "./model";

const METHODS: readonly Method[] = ["POST", "PUT", "PATCH", "DELETE"];

/** The path of the call with its query, as the console would send it. */
export function callTarget(proposal: Proposal): string {
  const query = new URLSearchParams(proposal.call.query.map(([name, value]) => [name, value]));
  const text = query.toString();
  return text === "" ? proposal.call.path : `${proposal.call.path}?${text}`;
}

/** What the person's call came to, as the server is told. Null when the person did not go through with it (they closed "Confirm it's you"). */
export async function carryOut(proposal: Proposal): Promise<OutcomeBody | null> {
  const method = METHODS.find((candidate) => candidate === proposal.call.method.toUpperCase());
  if (method === undefined) return { fingerprint: proposal.fingerprint, status: 400, output: `The call of this proposal is a ${proposal.call.method}, which the console does not make.` };
  let status = 0;
  try {
    const answer = await api<unknown>(method, callTarget(proposal), proposal.call.body ?? undefined, {
      headers: proposal.call.headers,
      onResponse: (response) => {
        status = response.status;
      },
    });
    const job = typeof answer === "object" && answer !== null ? (answer as { job_id?: unknown }).job_id : undefined;
    return { fingerprint: proposal.fingerprint, status: status === 0 ? 200 : status, ...(typeof job === "string" ? { job_id: job } : {}) };
  } catch (error: unknown) {
    // A second person decides: the call became a request of theirs, and the proposal says whose.
    if (error instanceof ApprovalPendingError && error.approvalId !== null) return { fingerprint: proposal.fingerprint, status: 202, approval: Number(error.approvalId) };
    // The person closed the question that asks to confirm it is them: nothing was done and the proposal still waits.
    if (error instanceof ElevationCancelledError) return null;
    if (error instanceof ApiError) return { fingerprint: proposal.fingerprint, status: error.status === 0 ? 599 : error.status, output: error.output ?? [error.detail, error.hint].filter((part): part is string => part !== null && part !== "").join("\n") };
    throw error;
  }
}

/**
 * Makes the call and reports it, and gives back the proposal as the server now holds it.
 *
 * @param conversation - The conversation the proposal is in.
 * @param proposal - What to carry out.
 * @returns The proposal the server answers with, or null when the person did not go through with it.
 */
export async function approve(conversation: string, proposal: Proposal): Promise<Proposal | null> {
  const outcome = await carryOut(proposal);
  if (outcome === null) return null;
  return asProposal(await reportOutcome(conversation, proposal.id, outcome));
}
