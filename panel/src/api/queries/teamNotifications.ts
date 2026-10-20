/**
 * The channels a team keeps for its own applications (`/api/teams/{team}/notifications/channels`).
 * They are the same channels as the server's, held to one team: each hears the applications of
 * that team and nothing else. Every rule - the types a team may use, how many it may have,
 * which credential is stored - is the server's; a credential is never in an answer (`***` says
 * one is stored, and sent back it keeps the stored one).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type TeamChannels = ResponseOf<"/api/teams/{team}/notifications/channels", "get">;
export type TeamChannel = TeamChannels["items"][number];
export type TeamChannelBody = BodyOf<"/api/teams/{team}/notifications/channels/{name}", "put">;

/** One type a team may add, and the settings it takes. */
export interface TeamChannelType {
  name: string;
  keys: readonly { key: string; secret: boolean; required: boolean }[];
}

export const teamChannelKeys = {
  all: ["teams", "notifications"] as const,
  list: (team: string) => ["teams", "notifications", team] as const,
};

export const teamChannelsQuery = (team: string) =>
  queryOptions({
    queryKey: teamChannelKeys.list(team),
    queryFn: ({ signal }) => request("get", "/api/teams/{team}/notifications/channels", { params: { team }, signal }),
  });

/**
 * The types a team may add, read from the untyped list the server answers (it is the server's
 * own table of types, so an entry this console cannot read is left out, not guessed).
 */
export function typesOf(raw: TeamChannels["types"]): TeamChannelType[] {
  const found: TeamChannelType[] = [];
  for (const entry of raw) {
    const name = entry["name"];
    const keys = entry["keys"];
    if (typeof name !== "string" || !Array.isArray(keys)) continue;
    found.push({
      name,
      keys: keys.flatMap((item: unknown) => {
        if (typeof item !== "object" || item === null) return [];
        const record = item as Record<string, unknown>;
        return typeof record["key"] === "string"
          ? [{ key: record["key"], secret: record["secret"] === true, required: record["required"] === true }]
          : [];
      }),
    });
  }
  return found;
}

export function saveTeamChannel(team: string, name: string, body: TeamChannelBody) {
  return request("put", "/api/teams/{team}/notifications/channels/{name}", { params: { team, name }, body });
}

export function removeTeamChannel(team: string, name: string) {
  return request("delete", "/api/teams/{team}/notifications/channels/{name}", { params: { team, name } });
}

export function testTeamChannel(team: string, name: string) {
  return request("post", "/api/teams/{team}/notifications/channels/{name}/test", { params: { team, name } });
}
