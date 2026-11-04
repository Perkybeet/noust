/**
 * The widgets about what the server runs: the applications and how they are, the latest deployments, the
 * scheduled tasks and the databases. A problem is the first thing each shows, and the only thing with colour
 * behind it: twenty applications that run are stone, and the one that fails is found.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import { appsQuery } from "../../../api/queries/apps";
import { cronJobsQuery } from "../../../api/queries/cron";
import { databasesQuery } from "../../../api/queries/databases";
import { deploymentsQuery } from "../../../api/queries/deployments";
import { logoFor } from "../../../features/databases/servers";
import { useFleetHome } from "../../../features/overview/useHome";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatCount, parseTimestamp } from "../../../lib/format";
import { RelativeTime } from "../../page/RelativeTime";
import { appStatus, deployStatus } from "../../page/status";
import { LogoMark } from "../../ui/LogoTile";
import { Mono } from "../../ui/Mono";
import { OBJECT_ICONS } from "../../ui/objectIcons";
import type { Status } from "../../ui/StatusPill";
import { STATE_RANK } from "../../page/status";
import { Big, Glyph, WidgetPending } from "../parts";
import type { WidgetBodyProps } from "../types";

// ---------------------------------------------------------------------------------------
// Applications

interface AppTile {
  key: string;
  domain: string;
  state: Status;
  node: string | null | undefined;
}

/** How many tiles each size draws before it says how many more run well. */
const TILES: Readonly<Record<string, number>> = { small: 0, medium: 5, tall: 5, wide: 11, large: 40, xlarge: 40 };

/**
 * Every application of the server, or of the fleet, as a tile with its state. One server's are read from its own list
 * (`apps.read`, all that a role of a team holds: the Overview is the machine's and is not asked); the fleet's from the
 * central's answer, which says which server each is on.
 */
function useTiles(scope: WidgetBodyProps["scope"], locale: T["locale"]): { tiles: AppTile[] | undefined; failed: boolean; retry: () => void } {
  const apps = useQuery({ ...appsQuery(), enabled: scope === "server" });
  const fleet = useFleetHome(scope === "fleet");
  return useMemo(() => {
    const sorted = (tiles: AppTile[]): AppTile[] => tiles.sort((a, b) => STATE_RANK[a.state] - STATE_RANK[b.state] || a.domain.localeCompare(b.domain));
    if (scope === "fleet") {
      const servers = fleet.servers;
      return {
        tiles: servers === undefined ? undefined : sorted(servers.flatMap((server) => server.apps.map((app) => ({ key: `${server.key}:${app.domain}`, domain: app.domain, state: appStatus(app.status, locale).state, node: server.node })))),
        failed: fleet.error !== null,
        retry: fleet.retry,
      };
    }
    return {
      tiles: apps.data === undefined ? undefined : sorted(apps.data.apps.map((app) => ({ key: app.domain, domain: app.domain, state: appStatus(app.status, locale).state, node: undefined }))),
      failed: apps.isError,
      retry: () => void apps.refetch(),
    };
  }, [scope, fleet.servers, fleet.error, fleet.retry, apps, locale]);
}

/** The applications, how many are in each state and a tile each, the ones with a problem first. */
export function AppsBody({ size, scope }: WidgetBodyProps) {
  const t = useT();
  const { tiles, failed, retry } = useTiles(scope, t.locale);
  if (tiles === undefined) return <WidgetPending title={t("widgets.apps.title")} failed={failed} onRetry={retry} />;
  const count = (states: readonly Status[]): number => tiles.filter((tile) => states.includes(tile.state)).length;
  const counts = { running: count(["running", "static"]), failed: count(["failed", "warning"]), deploying: count(["deploying", "queued"]), stopped: count(["stopped", "unknown"]) };
  const shown = tiles.slice(0, TILES[size] ?? 5);
  const rest = tiles.slice(shown.length);
  // What is left is said as running only when all of it is: a stopped application is never counted among the well ones.
  const restWell = rest.every((tile) => tile.state === "running" || tile.state === "static");
  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-baseline gap-3">
        <Big>{formatCount(tiles.length, t.locale)}</Big>
        <ul className="flex flex-wrap gap-x-3 gap-y-0.5 text-12">
          {[
            { state: "running", n: counts.running, word: t("widgets.apps.running") },
            { state: "failed", n: counts.failed, word: t("widgets.apps.failing") },
            { state: "deploying", n: counts.deploying, word: t("widgets.apps.deploying") },
            { state: "stopped", n: counts.stopped, word: t("widgets.apps.stopped") },
          ]
            .filter((entry) => entry.n > 0 || entry.state === "running")
            .map((entry) => (
              <li key={entry.state} className="inline-flex items-center gap-1 text-fg-muted">
                <Glyph state={entry.state as Status} size={10} />
                <span className="tabular-nums">{entry.n}</span>
                <span className="hidden w-mid:inline">{entry.word}</span>
              </li>
            ))}
        </ul>
      </div>
      <ul className="hidden min-h-0 flex-1 grid-cols-2 content-start gap-1.5 overflow-hidden w-mid:grid w-wide:grid-cols-3 w-xwide:grid-cols-6">
        {shown.map((tile) => {
          const problem = tile.state === "failed" || tile.state === "warning";
          const busy = tile.state === "deploying";
          return (
            <li key={tile.key}>
              <Link
                to="/apps/$domain"
                params={{ domain: tile.domain }}
                {...(tile.node !== undefined ? { search: { node: tile.node ?? undefined } as never } : {})}
                className={cx(
                  "flex h-8 items-center gap-2 rounded-chip border px-2 text-12 hover:bg-surface-hover",
                  problem ? "border-fail-border bg-fail-soft" : busy ? "border-warn-border bg-warn-soft" : "border-border bg-surface-raised",
                )}
              >
                <Glyph state={tile.state} size={10} />
                <span translate="no" className="min-w-0 flex-1 truncate font-medium text-fg">
                  {tile.domain}
                </span>
              </Link>
            </li>
          );
        })}
        {rest.length > 0 ? (
          <li>
            <Link to="/apps" className="flex h-8 items-center gap-1.5 rounded-chip px-2 text-12 font-medium text-fg-muted hover:text-fg">
              {restWell ? <Glyph state="running" size={10} /> : null}
              {restWell ? t("widgets.apps.more", { count: rest.length }) : t("widgets.apps.moreAny", { count: rest.length })}
            </Link>
          </li>
        ) : null}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Latest deployments

const DEPLOYMENTS_SHOWN = 8;

function commitOf(commit: string | null | undefined): string | null {
  return commit === null || commit === undefined || commit === "" ? null : commit.slice(0, 7);
}

/** The latest deployments of every application: where, what it said and how it ended; the newest alone in a tile. */
export function DeploymentsBody() {
  const t = useT();
  const deployments = useQuery(deploymentsQuery({ limit: DEPLOYMENTS_SHOWN }));
  if (deployments.data === undefined) return <WidgetPending title={t("widgets.deployments.title")} failed={deployments.isError} onRetry={() => void deployments.refetch()} />;
  const items = deployments.data.items;
  if (items.length === 0) return <p className="text-13 text-fg-muted">{t("widgets.deployments.none")}</p>;
  const newest = items[0];
  const newestView = newest === undefined ? null : deployStatus(newest.status, t.locale);
  return (
    <div className="flex h-full flex-col">
      {newest !== undefined && newestView !== null ? (
        <div className="min-w-0 w-mid:hidden">
          <p className="flex items-center gap-1.5 text-12 text-fg">
            <Glyph state={newestView.state} size={10} />
            {newestView.label}
          </p>
          <p translate="no" className="truncate text-13 font-medium text-fg">
            {newest.domain}
          </p>
          <RelativeTime value={newest.started_at} className="text-12 text-fg-muted" />
        </div>
      ) : null}
      <ul className="hidden min-h-0 flex-1 flex-col overflow-hidden w-mid:flex [&>li:nth-child(n+4)]:hidden w-tall:[&>li:nth-child(n+4)]:flex">
        {items.map((deployment) => {
          const view = deployStatus(deployment.status, t.locale);
          const commit = commitOf(deployment.git_commit);
          return (
            <li key={deployment.id} className="flex h-9 shrink-0 items-center gap-2.5 border-b border-border last:border-b-0">
              <Glyph state={view.state} />
              <Link to="/apps/$domain/deployments/$id" params={{ domain: deployment.domain, id: String(deployment.id) }} translate="no" className="w-36 shrink-0 truncate text-13 font-medium text-fg hover:underline w-wide:w-44">
                {deployment.domain}
              </Link>
              <span className="min-w-0 flex-1 truncate text-13 text-fg-muted">{deployment.commit_message ?? view.label}</span>
              {commit !== null ? <Mono tone="faint" className="hidden shrink-0 text-12 w-wide:inline">{commit}</Mono> : null}
              <RelativeTime value={deployment.started_at} className="shrink-0 text-12 text-fg-faint" />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Scheduled tasks

/** The scheduled tasks that run next: the very next in a tile, a line each in a band. */
export function TasksBody() {
  const t = useT();
  const cron = useQuery(cronJobsQuery());
  if (cron.data === undefined) return <WidgetPending title={t("widgets.tasks.title")} failed={cron.isError} onRetry={() => void cron.refetch()} />;
  const upcoming = cron.data.jobs
    .filter((job) => job.enabled && parseTimestamp(job.next_run) !== null)
    .sort((a, b) => (parseTimestamp(a.next_run)?.getTime() ?? 0) - (parseTimestamp(b.next_run)?.getTime() ?? 0));
  if (upcoming.length === 0) return <p className="text-13 text-fg-muted">{t("widgets.tasks.none")}</p>;
  const first = upcoming[0];
  return (
    <div className="flex h-full flex-col gap-1.5">
      {first !== undefined ? (
        <div className="min-w-0 w-wide:hidden">
          <Mono className="block truncate text-13">{first.name}</Mono>
          <RelativeTime value={first.next_run} className="text-12 text-fg-muted" />
          {upcoming.length > 1 ? <p className="mt-1 text-12 text-fg-faint">{t("widgets.tasks.more", { count: upcoming.length - 1 })}</p> : null}
        </div>
      ) : null}
      <ul className="hidden flex-col gap-1.5 overflow-hidden w-wide:flex">
        {upcoming.slice(0, 6).map((job) => (
          <li key={job.name} className="flex items-center gap-3 text-13">
            <Mono className="w-36 shrink-0 truncate">{job.name}</Mono>
            <span translate="no" className="min-w-0 flex-1 truncate text-12 text-fg-muted">
              {job.app_domain}
            </span>
            <RelativeTime value={job.next_run} className="shrink-0 text-12 text-fg" />
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Databases

function backupWords(t: T, last: string | null | undefined): { text: string; well: boolean } {
  return last === null || last === undefined || last === "" ? { text: t("widgets.databases.noBackup"), well: false } : { text: t("widgets.databases.backedUp"), well: true };
}

/** The databases, the biggest first: their engine, their size and whether they have a backup. */
export function DatabasesBody() {
  const t = useT();
  const databases = useQuery(databasesQuery());
  if (databases.data === undefined) return <WidgetPending title={t("widgets.databases.title")} failed={databases.isError} onRetry={() => void databases.refetch()} />;
  const list = databases.data.databases.filter((database) => !database.missing);
  if (list.length === 0) return <p className="text-13 text-fg-muted">{t("widgets.databases.none")}</p>;
  return (
    <ul className="flex h-full flex-col overflow-hidden [&>li:nth-child(n+4)]:hidden w-tall:[&>li:nth-child(n+4)]:flex">
      {list.slice(0, 8).map((database) => {
        const logo = logoFor(database.engine.split("@")[0] ?? database.engine);
        const backup = backupWords(t, database.last_backup);
        return (
          <li key={`${database.engine}/${database.name}`} className="flex h-9 shrink-0 items-center gap-2.5 border-b border-border last:border-b-0">
            {logo !== null ? <LogoMark logo={logo} className="size-icon-md shrink-0" /> : <OBJECT_ICONS.database aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />}
            <Link to="/databases/$engine/$name" params={{ engine: database.engine, name: database.name }} translate="no" className="min-w-0 flex-1 truncate text-13 font-medium text-fg hover:text-accent-fg">
              {database.name}
            </Link>
            {database.size !== null && database.size !== undefined ? <span className="w-16 shrink-0 text-right text-12 text-fg-muted tabular-nums">{database.size}</span> : null}
            <span className={cx("hidden w-28 shrink-0 items-center justify-end gap-1 text-12 w-wide:flex", backup.well ? "text-fg-muted" : "text-fg")}>
              {backup.well ? null : <Glyph state="warning" size={10} />}
              {backup.text}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
