/**
 * What the console reads before it offers a terminal: whether the server allows one, and whether
 * the signed-in account may open it.
 *
 * Both are only the offer. The server decides again when the ticket is asked for, and again when
 * the socket opens, so a stale answer here costs a refusal and never a shell.
 */

import { queryOptions, useQuery } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { sessionQuery } from "../../api/queries/auth";

export type TerminalStatus = ResponseOf<"/api/terminal/status", "get">;

export const terminalKeys = {
  status: ["terminal", "status"] as const,
  recordings: ["terminal", "recordings"] as const,
  recording: (id: string) => ["terminal", "recordings", id] as const,
};

/** What the terminal recorded, newest session first: the sessions that are open and the ones that ended. Takes `audit.read`. */
export const terminalRecordingsQuery = () =>
  queryOptions({
    queryKey: terminalKeys.recordings,
    queryFn: ({ signal }) => request("get", "/api/terminal/recordings", { signal }),
  });

/** One recording, with what was printed in it and whether its hash still holds. */
export const terminalRecordingQuery = (id: string) =>
  queryOptions({
    queryKey: terminalKeys.recording(id),
    queryFn: ({ signal }) => request("get", "/api/terminal/recordings/{recording_id}", { params: { recording_id: id }, signal }),
  });

/** The switches and limits of the terminal on the server on screen. */
export const terminalStatusQuery = () =>
  queryOptions({
    queryKey: terminalKeys.status,
    queryFn: ({ signal }) => request("get", "/api/terminal/status", { signal }),
    // A switch turned on from a terminal on the server is found by opening the page again.
    staleTime: 15_000,
  });

export type TerminalPermission = "apps.terminal" | "server.terminal";

/**
 * Whether the signed-in account holds a permission: `undefined` while the session is read, so a
 * page does not flash "your account cannot" at someone who can.
 */
export function useHasPermission(permission: TerminalPermission): boolean | undefined {
  const { data } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  return data === undefined ? undefined : data.includes(permission);
}
