/**
 * Who has entered the machine, as one list: the SSH logins the server read from its journal and the sessions of the
 * console's terminal with their recordings. Pure, so each rule is tested without drawing a table.
 */

import type { ResponseOf } from "../../../api/client";
import type { SshStatus } from "../queries";

export type RecordingList = ResponseOf<"/api/terminal/recordings", "get">;
export type Recording = RecordingList["recordings"][number];
export type RecordingView = ResponseOf<"/api/terminal/recordings/{recording_id}", "get">;

/** How somebody got in: over SSH, or through the console's terminal (which a recording remembers). */
export type EntryWay = "ssh" | "console";

export interface AccessRow {
  key: string;
  /** The account that entered: the machine's own for SSH, the console's for its terminal. */
  who: string;
  way: EntryWay;
  /** For SSH, how the login was proved (`publickey`, `password`); null for the console. */
  method: string | null;
  /** The address it came from, when known. */
  from: string | null;
  /** Unix seconds; null when the source did not say. */
  at: number | null;
  /** The session is still open: it is a person who is there now. */
  live: boolean;
  /** The recording to read, when the console kept one. */
  recording: string | null;
}

/** The SSH side of the list: the sessions open now, then the logins the journal has, newest first. */
export function sshRows(ssh: Pick<SshStatus, "sessions" | "logins">): AccessRow[] {
  const live = ssh.sessions.map(
    (session): AccessRow => ({ key: `ssh-live:${session.peer}:${String(session.port)}`, who: session.user ?? session.peer, way: "ssh", method: null, from: session.peer, at: null, live: true, recording: null }),
  );
  const past = ssh.logins.recent.map((login): AccessRow => ({ key: `ssh:${String(login.at)}:${login.source}:${login.user}`, who: login.user, way: "ssh", method: login.method, from: login.source, at: login.at, live: false, recording: null }));
  return [...live, ...past];
}

/** The console's side of the list: every terminal session it recorded, with the one to read when it kept one. */
export function consoleRows(recordings: readonly Recording[]): AccessRow[] {
  return recordings.map(
    (recording): AccessRow => ({
      key: `console:${recording.id}`,
      who: recording.actor ?? recording.account ?? "",
      way: "console",
      method: null,
      from: recording.client_ip ?? null,
      at: recording.started_at ?? null,
      live: recording.status === "recording",
      // An interrupted one still has what was recorded until it stopped.
      recording: recording.status === "recording" ? null : recording.id,
    }),
  );
}

/** Both sides in one list: who is there now first, then the newest. */
export function accessRows(ssh: Pick<SshStatus, "sessions" | "logins"> | undefined, recordings: readonly Recording[] | undefined): AccessRow[] {
  const rows = [...(ssh === undefined ? [] : sshRows(ssh)), ...(recordings === undefined ? [] : consoleRows(recordings))];
  return rows.sort((a, b) => Number(b.live) - Number(a.live) || (b.at ?? 0) - (a.at ?? 0));
}
