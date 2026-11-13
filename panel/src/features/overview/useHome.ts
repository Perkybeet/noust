/**
 * What Home reads, for one server and for the whole fleet, as one shape (`HomeData`): the servers with
 * their applications, the one list of what needs attention, and what happened. The page that draws it
 * does not know which of the two it is showing, which is how Home has one board, one set of courses and
 * one timeline instead of one of each for the server and another for the fleet.
 *
 * One server: `GET /api/overview` and `GET /api/apps`, and the one list of what needs attention (`useAttention`). The fleet:
 * `GET /api/fleet/summary` (every server's own Overview, verbatim) and `GET /api/views/apps`, and what is the person's
 * own (`GET /api/attention?only=own`), which a central adds to the lists of its servers.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { appsQuery } from "../../api/queries/apps";
import { overviewQuery } from "../../api/queries/overview";
import type { OverviewAttentionItem } from "../../api/queries/overview";
import { appsViewQuery } from "../../api/queries/views";
import { fleetViewQuery, objectOf, textOf } from "../fleet/data";
import type { FleetRow, FleetView } from "../fleet/data";
import { useAttention, useOwnAttention } from "../inbox/useAttention";
import { useScopeOffers } from "../../nodes/useNode";
import { ACTIVITY_SHOWN, attentionEntries, fleetServerHome, serverHome, timelineOf } from "./homeData";
import type { ActivityLine, HomeApp, HomeAttention, HomeServer } from "./homeData";
import { isEmptyServer } from "./overviewData";

export interface HomeData {
  /** The servers, this one first; undefined until they are read. */
  servers: readonly HomeServer[] | undefined;
  /** What needs attention, worst first; undefined until it is read. */
  entries: readonly HomeAttention[] | undefined;
  /** How many there are in all: a server may have cut its own list. */
  total: number;
  /** What happened, newest first; undefined until it is read. */
  lines: readonly ActivityLine[] | undefined;
  /** Nothing runs yet anywhere: Home is a list of first steps. */
  empty: boolean;
  /** Whether a backup is already scheduled somewhere, which the first steps tick. */
  backupsScheduled: boolean;
  /** More than one server to tell apart: only then is a server named on a card. */
  several: boolean;
  /** The fleet's answer, for the servers that did not answer fully; undefined for one server. */
  view: FleetView | undefined;
  /** Why Home could not be read at all, when it could not. */
  error: unknown;
  retry: () => void;
  retrying: boolean;
}

const NONE: readonly OverviewAttentionItem[] = [];

/**
 * This server's Home, or the selected node's: the page is on it, so its links stay there. `enabled` is false for
 * the panel that is the whole fleet, which reads the fleet's own answers and asks this server nothing of its own.
 */
export function useServerHome(enabled = true): HomeData {
  const overview = useQuery({ ...overviewQuery(), enabled });
  // A server that cannot deploy (a hub) has no applications to ask for, and its Home is never "empty, deploy something": what it is for is
  // the fleet, and its panel shows its machine (item 187).
  const deploys = useScopeOffers()("applications");
  const apps = useQuery({ ...appsQuery(), enabled: enabled && deploys });
  const attention = useAttention(enabled);
  const servers = useMemo(() => {
    const list = deploys ? apps.data?.apps : [];
    if (overview.data === undefined || list === undefined) return undefined;
    const homeApps: HomeApp[] = list.map((app) => ({ domain: app.domain, status: app.status }));
    return [serverHome(overview.data, homeApps, NONE)];
  }, [overview.data, apps.data, deploys]);
  const entries = useMemo(
    () => (attention.loading || overview.data === undefined ? undefined : attention.items.map((item): HomeAttention => ({ key: item.id, item, server: null }))),
    [attention.items, attention.loading, overview.data],
  );
  const lines = useMemo(() => (servers === undefined ? undefined : timelineOf(servers, ACTIVITY_SHOWN, false)), [servers]);
  return {
    servers,
    entries,
    total: attention.total,
    lines,
    empty: overview.data !== undefined && deploys && isEmptyServer(overview.data),
    backupsScheduled: (overview.data?.backups.scheduled ?? 0) > 0,
    several: false,
    view: undefined,
    // Nothing to show and a failed read: what is on screen already is kept through a refresh that fails.
    error: overview.data === undefined ? overview.error : null,
    retry: () => void overview.refetch(),
    retrying: overview.isRefetching,
  };
}

/** How many a row's own Overview says it has scheduled, read field by field: the contract types a row's Overview as an open dictionary. */
function scheduledOf(row: FleetRow): number {
  const backups = objectOf(objectOf(row["overview"])?.["backups"]);
  const scheduled = backups?.["scheduled"];
  return typeof scheduled === "number" ? scheduled : 0;
}

/** The whole fleet's Home: every server's own list, applications and activity, side by side. `enabled` is false for a panel of one server. */
export function useFleetHome(enabled = true): HomeData {
  const summary = useQuery({ ...fleetViewQuery("summary"), enabled });
  const views = useQuery({ ...appsViewQuery(), enabled });
  const extras = useOwnAttention(enabled);
  const servers = useMemo(() => {
    if (summary.data === undefined || views.data === undefined) return undefined;
    const rows = [...summary.data.items].sort((a, b) => (a["local"] === true ? -1 : b["local"] === true ? 1 : (textOf(a["name"]) ?? "").localeCompare(textOf(b["name"]) ?? "")));
    return rows.map((row) => {
      const name = textOf(row["name"]) ?? textOf(row["node"]) ?? "";
      const apps = views.data.items.filter((item) => item["node"] === name).map((item): HomeApp => ({ domain: textOf(item["domain"]) ?? "", status: textOf(item["status"]) ?? "" }));
      return fleetServerHome(row, apps.filter((app) => app.domain !== ""));
    });
  }, [summary.data, views.data]);
  const own = useMemo(() => {
    const central = servers?.find((server) => server.node === null);
    return central === undefined ? null : { name: central.name, node: null };
  }, [servers]);
  const entries = useMemo(() => (servers === undefined ? undefined : attentionEntries(servers, extras, own)), [servers, extras, own]);
  const lines = useMemo(() => (servers === undefined ? undefined : timelineOf(servers, ACTIVITY_SHOWN, true)), [servers]);
  const total = useMemo(() => {
    if (summary.data === undefined) return 0;
    // Each server may have cut its own list: what it counted in all, and what the console adds.
    const counted = summary.data.items.reduce((sum, row) => {
      const overview = objectOf(row["overview"]);
      const all = overview?.["attention_total"];
      return sum + (typeof all === "number" ? all : 0);
    }, 0);
    return counted + extras.length;
  }, [summary.data, extras]);
  const error = (summary.data === undefined ? summary.error : null) ?? (views.data === undefined ? views.error : null);
  return {
    servers,
    entries,
    total,
    lines,
    // A server that does not answer may be running everything: nothing is known to be missing until all have said.
    empty: servers?.every((server) => server.apps.length === 0 && !server.unreachable) === true,
    backupsScheduled: summary.data?.items.some((row) => scheduledOf(row) > 0) === true,
    several: (servers?.length ?? 0) > 1,
    view: summary.data,
    error,
    retry: () => {
      void summary.refetch();
      void views.refetch();
    },
    retrying: summary.isRefetching || views.isRefetching,
  };
}

/** The Home a panel draws from: its servers, what needs attention and what happened, for one server or for the whole fleet. */
export function useScopedHome(scope: "server" | "fleet"): HomeData {
  const server = useServerHome(scope === "server");
  const fleet = useFleetHome(scope === "fleet");
  return scope === "fleet" ? fleet : server;
}
