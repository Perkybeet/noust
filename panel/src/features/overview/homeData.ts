/**
 * What Home is made of, as pure functions so each rule is tested without a page: the courses of the
 * fleet (a server and its applications), the legend of how many are in each state, the one list of
 * what needs attention across servers, and the one timeline of what happened.
 *
 * Home reads two things that look different and are the same shape: one server (`GET /api/overview`
 * and `GET /api/apps`) and a fleet (`GET /api/fleet/summary`, which carries each server's own Overview
 * verbatim, and `GET /api/views/apps`). Both become `HomeServer`s here, so the page that draws them
 * has one implementation of "the fleet in courses", of "what needs attention" and of "what happened".
 */

import type { Overview, OverviewActivityEntry, OverviewAttentionItem, OverviewAttentionReason } from "../../api/queries/overview";
import type { StatusView } from "../../components/page/status";
import type { FleetCourse, FleetCourseApp } from "../../components/ui/FleetCourses";
import type { CodedText } from "../../lib/serverSentence";
import type { Status } from "../../components/ui/StatusPill";
import { parseTimestamp } from "../../lib/format";
import { attentionOf, numberOf, objectOf, textOf } from "../fleet/data";
import type { FleetRow } from "../fleet/data";
import { withNoAnswer } from "./overviewData";

/** One application as a course draws it: its name and the state word the server gave. */
export interface HomeApp {
  domain: string;
  status: string;
}

/** One server of Home: this one, or a node of the fleet. */
export interface HomeServer {
  /** Unique: the node's name, or `@local` for the server the console is on. */
  key: string;
  /** The name the operator gave it. */
  name: string;
  /**
   * The node to open it on, null for the central itself, or undefined when the page is already on the
   * server and its links stay where the page is (a server's own Home, which may be a node's).
   */
  node: string | null | undefined;
  /** The server did not answer, or the central could not read it (not an old Noust: that is not a fault). */
  unreachable: boolean;
  /** How full its disk is, 0 to 100, when it said. */
  diskPercent: number | null;
  apps: readonly HomeApp[];
  /** What it says needs attention, in the Overview's own shape. */
  attention: readonly OverviewAttentionItem[];
  /** What it says happened lately. */
  activity: readonly OverviewActivityEntry[];
}

/** The server this console is on, from its own Overview and its applications. */
export function serverHome(overview: Overview, apps: readonly HomeApp[], attention: readonly OverviewAttentionItem[]): HomeServer {
  return {
    key: "@local",
    name: overview.server.name,
    node: undefined,
    unreachable: false,
    diskPercent: overview.disk.error ? null : overview.disk.percent,
    apps,
    attention,
    activity: overview.activity ?? [],
  };
}

const UNREACHABLE: ReadonlySet<string> = new Set(["unreachable", "refused"]);

/** A sentence a node sent as a code and its values, or null when it sent none (a node older than this console). */
function codedOf(raw: unknown): CodedText | null {
  const sentence = objectOf(raw);
  const code = textOf(sentence?.["code"]);
  if (sentence === null || code === null) return null;
  const params = objectOf(sentence["params"]);
  return params === null ? { code } : { code, params: params as NonNullable<CodedText["params"]> };
}

/** A node's activity, read field by field: the contract types a row's Overview as an open dictionary. */
function activityOf(overview: Record<string, unknown> | null): OverviewActivityEntry[] {
  const list = overview !== null && Array.isArray(overview["activity"]) ? overview["activity"] : [];
  return list.flatMap((raw): OverviewActivityEntry[] => {
    const entry = objectOf(raw);
    const id = textOf(entry?.["id"]);
    const type = textOf(entry?.["type"]);
    if (entry === null || id === null || type === null) return [];
    return [
      {
        id,
        type,
        title: textOf(entry["title"]) ?? "",
        title_coded: codedOf(entry["title_coded"]),
        status: textOf(entry["status"]) ?? "",
        domain: textOf(entry["domain"]),
        actor: textOf(entry["actor"]),
        actor_name: textOf(entry["actor_name"]),
        at: textOf(entry["at"]),
      },
    ];
  });
}

/** A server of the fleet's summary (a row carries its own Overview), with the applications the apps view gave for it. */
export function fleetServerHome(row: FleetRow, apps: readonly HomeApp[]): HomeServer {
  const local = row["local"] === true;
  const name = textOf(row["name"]) ?? textOf(row["node"]) ?? "";
  const overview = objectOf(row["overview"]);
  const disk = objectOf(overview?.["disk"]);
  const percent = disk === null || typeof disk["error"] === "string" ? null : numberOf(disk["percent"]);
  // What the node's own list cannot see, a service that runs and answers nothing, is added from its applications, as for this server.
  const attention = withNoAnswer(
    (attentionOf(row)?.items ?? []).map((item) => ({ ...item, reasons: item.reasons as unknown as OverviewAttentionReason[] })),
    apps,
  );
  return {
    key: local ? "@local" : name,
    name,
    node: local ? null : name,
    unreachable: UNREACHABLE.has(textOf(row["reachability"]) ?? ""),
    diskPercent: percent,
    apps,
    attention,
    activity: activityOf(overview),
  };
}

// ---------------------------------------------------------------------------------------
// The courses

export interface CourseWords {
  /** The state the console gives a server's word for an application (`appStatus`). */
  describe: (status: string) => StatusView;
  /** What is said of a server that does not answer. */
  unreachable: string;
}

/** The fleet as courses: this server first, then the others in the order given. */
export function coursesOf(servers: readonly HomeServer[], words: CourseWords): FleetCourse[] {
  return servers.map((server): FleetCourse => {
    const apps = server.apps.map((app): FleetCourseApp => {
      const view = words.describe(app.status);
      return { id: app.domain, name: app.domain, state: view.state, stateLabel: view.label };
    });
    return {
      id: server.key,
      name: server.name,
      ...(server.diskPercent !== null ? { diskPercent: server.diskPercent } : {}),
      ...(server.unreachable ? { problem: { state: "failed", label: words.unreachable } } : {}),
      apps,
    };
  });
}

export interface LegendEntry {
  state: Status;
  count: number;
}

/** What the legend over the courses counts, in the order it is read: how many are well, then each state with any. */
const LEGEND: readonly { state: Status; members: readonly Status[] }[] = [
  { state: "running", members: ["running", "static"] },
  { state: "deploying", members: ["deploying", "queued"] },
  { state: "failed", members: ["failed"] },
  { state: "warning", members: ["warning"] },
  { state: "stopped", members: ["stopped"] },
  { state: "unknown", members: ["unknown"] },
];

/** How many applications are in each state, running first; a state nothing is in is not listed. */
export function legendOf(courses: readonly FleetCourse[]): LegendEntry[] {
  const states = courses.flatMap((course) => course.apps.map((app) => app.state));
  return LEGEND.map(({ state, members }) => ({ state, count: states.filter((candidate) => members.includes(candidate)).length })).filter((entry) => entry.count > 0);
}

// ---------------------------------------------------------------------------------------
// What needs attention

/** One thing that needs the operator, with the server it is on when the console has more than one. */
export interface HomeAttention {
  /** Unique across servers: an application of the same name on two servers is two cards. */
  key: string;
  item: OverviewAttentionItem;
  /** The server it is on; null for what is the console's own (a request waiting, a newer Noust). */
  server: { name: string; node: string | null | undefined } | null;
}

const RANK: Readonly<Record<string, number>> = { fail: 0, warn: 1 };

/**
 * The one list of what needs attention, worst first and then by server and title: each server's own
 * list, and what the console adds (`extras`, with the server it is the console's own). The same
 * `OverviewAttentionItem`s the inbox and the sidebar's counters are made of, read once.
 */
export function attentionEntries(servers: readonly HomeServer[], extras: readonly OverviewAttentionItem[], own: { name: string; node: string | null | undefined } | null): HomeAttention[] {
  const entries: HomeAttention[] = [
    ...servers.flatMap((server) => server.attention.map((item) => ({ key: `${server.key}:${item.id}`, item, server: { name: server.name, node: server.node } }))),
    ...extras.map((item) => ({ key: `extra:${item.id}`, item, server: own })),
  ];
  const position = new Map(servers.map((server, index) => [server.name, index]));
  return entries.sort(
    (a, b) =>
      (RANK[a.item.severity] ?? 2) - (RANK[b.item.severity] ?? 2) ||
      (position.get(a.server?.name ?? "") ?? 0) - (position.get(b.server?.name ?? "") ?? 0) ||
      a.item.title.toLowerCase().localeCompare(b.item.title.toLowerCase()),
  );
}

/** When an item started to need attention, as its reasons say it; null when none says. */
export function sinceOf(item: OverviewAttentionItem): string | null {
  return item.reasons.find((reason) => reason.when)?.when ?? null;
}

// ---------------------------------------------------------------------------------------
// What happened

/** How many things that happened a panel keeps to show: the widget draws the first ones its room has for. */
export const ACTIVITY_SHOWN = 8;

/** One thing that happened, with the server it happened on when the console has more than one. */
export interface ActivityLine {
  key: string;
  entry: OverviewActivityEntry;
  at: Date | null;
  /** The server's name, when the console has more than one to tell apart. */
  server: string | null;
  node: string | null | undefined;
}

/** The newest `limit` things that happened on any server, newest first; an entry with no moment is the oldest. */
export function timelineOf(servers: readonly HomeServer[], limit: number, named: boolean): ActivityLine[] {
  const lines = servers.flatMap((server) =>
    server.activity.map((entry): ActivityLine => ({ key: `${server.key}:${entry.id}`, entry, at: parseTimestamp(entry.at ?? null), server: named ? server.name : null, node: server.node })),
  );
  return lines.sort((a, b) => (b.at?.getTime() ?? Number.NEGATIVE_INFINITY) - (a.at?.getTime() ?? Number.NEGATIVE_INFINITY)).slice(0, limit);
}
