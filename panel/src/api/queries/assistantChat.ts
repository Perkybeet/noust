/**
 * The conversations of the built-in assistant (`/api/assistant/conversations`, item 171): beginning one, asking (the answer arrives as the
 * `assistant` event of `/events`), reading what each turn did, stopping a turn and deciding a proposal. A conversation is its author's
 * alone: another person's is a 404 with the body of one that does not exist.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type Conversations = ResponseOf<"/api/assistant/conversations", "get">;
export type ConversationSummary = Conversations[number];
export type TurnStarted = ResponseOf<"/api/assistant/conversations/{conversation_id}/turns", "post">;
export type AskBody = BodyOf<"/api/assistant/conversations/{conversation_id}/turns", "post">;
export type TurnsList = ResponseOf<"/api/assistant/conversations/{conversation_id}/turns", "get">;
export type OutcomeBody = BodyOf<"/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}/outcome", "post">;

export const assistantChatKeys = {
  list: ["assistant", "conversations"] as const,
  turns: (conversation: string) => ["assistant", "turns", conversation] as const,
};

export const conversationsQuery = () =>
  queryOptions({
    queryKey: assistantChatKeys.list,
    queryFn: ({ signal }) => request("get", "/api/assistant/conversations", { signal }),
  });

export const turnsQuery = (conversation: string) =>
  queryOptions({
    queryKey: assistantChatKeys.turns(conversation),
    queryFn: ({ signal }) => request("get", "/api/assistant/conversations/{conversation_id}/turns", { params: { conversation_id: conversation }, signal }),
  });

export function beginConversation() {
  return request("post", "/api/assistant/conversations", { body: { title: "" } });
}

/** Asks a question; the server answers at once and the answer arrives on the event stream. */
export function askTurn(conversation: string, body: AskBody) {
  return request("post", "/api/assistant/conversations/{conversation_id}/turns", { params: { conversation_id: conversation }, body });
}

/** Stops a turn between two steps, never in the middle of an action. */
export function cancelTurn(conversation: string, turn: string) {
  return request("post", "/api/assistant/conversations/{conversation_id}/turns/{turn_id}/cancel", { params: { conversation_id: conversation, turn_id: turn } });
}

export function deleteConversation(conversation: string) {
  return request("delete", "/api/assistant/conversations/{conversation_id}", { params: { conversation_id: conversation } });
}

/** What the browser says came of a call it made for a proposal; the server checks the audit trail before it believes it. */
export function reportOutcome(conversation: string, proposal: string, body: OutcomeBody) {
  return request("post", "/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}/outcome", { params: { conversation_id: conversation, proposal_id: proposal }, body });
}

export function rejectProposal(conversation: string, proposal: string) {
  return request("post", "/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}/reject", { params: { conversation_id: conversation, proposal_id: proposal } });
}
