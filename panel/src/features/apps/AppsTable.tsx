import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { AppStatePill } from "../../components/page/AppStatePill";
import { RelativeTime } from "../../components/page/RelativeTime";
import { STATE_RANK, appStatus, deployStatus } from "../../components/page/status";
import { Checkbox } from "../../components/ui/Checkbox";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { parseTimestamp } from "../../lib/format";
import { inRows, ownerColumns } from "../teams/ownership";
import type { Labels, Scope } from "../teams/organization";
import type { AppInfo, Deployment, LastDeployment } from "./data";
import { deployMoment, lastDeployOf, previewParentOf, useTypeName } from "./data";
import type { NodeOutcome } from "../fleet/data";
import { linkOfHref } from "../fleet/links";
import { ServerCell } from "../fleet/parts";
import { AppTypeMark } from "./AppTypeMark";

function isFleetApp(app: AppInfo | FleetApp): app is FleetApp {
  return "node" in app && typeof app.node === "string";
}

/**
 * A deployment's outcome as a glyph and when it happened, for dense rows. A deploy an operator
 * cancelled is stored as failed and is not one: it reads as cancelled, not as a fault.
 */
export function DeployMoment({ deploy }: { deploy: Pick<Deployment, "status" | "finished_at"> & { started_at?: string | null; cancelled?: boolean | null } }) {
  const t = useT();
  const view = deployStatus(deploy.cancelled === true ? "cancelled" : deploy.status, t.locale);
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusGlyph state={view.state} size={10} className={stateTextClass(view.state)} />
      <span className="sr-only">{`${view.label}, `}</span>
      <RelativeTime value={deployMoment(deploy)} className="text-fg-muted" />
    </span>
  );
}

/** The tag a pull request preview wears: what it is, in the place a tag is, so no row has a second line (item 122). */
const PREVIEW_TAG = "preview";

/**
 * The app's domain, a link to its page, with its tags after it on the same line. A pull request preview wears one more,
 * "preview", and says whose it is to a screen reader and in the tag's own title, so a list with previews in it still
 * reads as the apps the operator deployed and every row is as tall as the others.
 */
export function AppName({ t, app, href }: { t: T; app: AppInfo; href?: string }) {
  const parent = previewParentOf(app);
  const className = "-mx-1 block max-w-full truncate rounded-chip px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2";
  // A row of the fleet opens the application on the server it runs on (`/n/web-2/apps/shop.example.com`).
  const there = href === undefined ? null : linkOfHref(href);
  const link =
    there === null ? (
      <Link to="/apps/$domain" params={{ domain: app.domain }} title={app.domain} className={className}>
        {app.domain}
      </Link>
    ) : (
      <Link to={there.to} search={there.search as never} title={app.domain} className={className}>
        {app.domain}
      </Link>
    );
  const tags = [...(parent !== null ? [PREVIEW_TAG] : []), ...(app.tags ?? [])];
  if (tags.length === 0) return link;
  return (
    <>
      <NameWithTags
        name={link}
        tags={tags}
        title={t("appTags.row.label")}
        renderTag={(tag) => (tag === PREVIEW_TAG && parent !== null ? <span title={t("apps.table.previewOf", { parent })}>{t("apps.table.previewTag")}</span> : tag)}
      />
      {parent !== null ? <span className="sr-only">{t("apps.table.previewOf", { parent })}</span> : null}
    </>
  );
}

interface Row {
  app: AppInfo | FleetApp;
  deploy: LastDeployment | Deployment | null;
}

/** An application of the fleet's list: the server's own row, and which server it is and where its page is. */
export type FleetApp = AppInfo & { node: string; local: boolean; href: string };

export interface AppsTableProps {
  apps: readonly (AppInfo | FleetApp)[];
  /** The newest deploy of each domain (latestDeployByDomain), when the page read the history. */
  deploys: ReadonlyMap<string, Deployment>;
  caption: string;
  loading?: boolean;
  /** How many rows to draw while loading: the count the page expects, so nothing jumps. */
  skeletonRows?: number;
  /** Shown in place of rows when there are none. */
  empty?: ReactNode;
  /** A per-row menu; the list offers one. On a phone it stays in view on every card. */
  rowActions?: (app: AppInfo) => ReactNode;
  /** Says where each application lives, when more than one place is in view (teams, projects, environments). */
  owner?: { scope: Scope; labels: Labels };
  /**
   * The list is the fleet's: each row names its server, opens on it, and the server's answer (old, down) is said
   * under its name. The rows are `FleetApp`s.
   */
  fleet?: { outcomes: ReadonlyMap<string, NodeOutcome> };
  /** The applications chosen for an action on several (checklist M-08): a checkbox on each row. */
  selection?: { selected: ReadonlySet<string>; onSelect: (domain: string, checked: boolean) => void };
  /** The table sits in a `SurfaceSection` that is already a slab (a project's group of the list): it draws only its rows. */
  embedded?: boolean;
  className?: string;
}

/**
 * Every application, identity first: its domain with its tags (a link to it, so it opens in a new tab like any other
 * link), its state, its type by the name people know it by and its last deploy; where there is more than one place
 * to tell apart, its project and its environment, and on a fleet, its server. It is the one table of applications: the
 * list, a project's and the fleet's are this, and the views of the list (by project, by state) are this under a heading
 * each, never another table. On a phone each row is a card with its actions always in view.
 */
export function AppsTable({ apps, deploys, caption, loading = false, skeletonRows, empty, rowActions, owner, fleet, selection, embedded = false, className }: AppsTableProps) {
  const t = useT();
  const typeName = useTypeName();
  const rows: Row[] = apps.map((app) => ({ app, deploy: lastDeployOf(app, deploys) }));

  const columns: Column<Row>[] = [
    ...(selection !== undefined
      ? ([
          {
            id: "select",
            header: t("apps.bulk.selectColumn"),
            width: "w-12",
            // On a phone too, in a slot of its own: choosing applications for an action is this view's job.
            card: "control",
            cell: (row) => (
              <Checkbox
                aria-label={t("apps.bulk.selectOne", { domain: row.app.domain })}
                checked={selection.selected.has(row.app.domain)}
                onCheckedChange={(checked) => {
                  selection.onSelect(row.app.domain, checked);
                }}
              />
            ),
          },
        ] satisfies Column<Row>[])
      : []),
    {
      id: "domain",
      header: t("apps.table.columnApplication"),
      // Named: the first column is the title of a card by default, and with the selection column before it this one was
      // drawn as a value at the end of a line, cut to a dozen characters, under an empty title.
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 items-center gap-3">
          <AppTypeMark type={row.app.app_type} />
          <AppName t={t} app={row.app} {...(isFleetApp(row.app) ? { href: row.app.href } : {})} />
        </span>
      ),
      sortValue: (row) => row.app.domain,
    },
    ...(fleet !== undefined
      ? ([
          {
            id: "server",
            header: t("apps.table.columnServer"),
            width: "w-44",
            card: "meta",
            sortValue: (row) => (isFleetApp(row.app) ? row.app.node : ""),
            cell: (row) =>
              isFleetApp(row.app) ? (
                <ServerCell origin={{ node: row.app.node, local: row.app.local, href: row.app.href }} outcome={fleet.outcomes.get(row.app.node)} />
              ) : null,
          },
        ] satisfies Column<Row>[])
      : []),
    {
      id: "state",
      header: t("apps.table.columnState"),
      width: "w-32",
      card: "status",
      cell: (row) => <AppStatePill status={row.app.status} appearance="inline" size="sm" />,
      sortValue: (row) => STATE_RANK[appStatus(row.app.status).state],
    },
    {
      id: "type",
      header: t("apps.table.columnType"),
      width: "w-36",
      hideBelow: "sm",
      cell: (row) => {
        const name = typeName(row.app.app_type);
        return name === null ? <EmptyCell reason={t("apps.table.unknownType")} /> : <span className="text-fg-muted">{name}</span>;
      },
      sortValue: (row) => typeName(row.app.app_type),
    },
    ...(owner !== undefined ? ownerColumns<AppInfo>(t, owner.scope, owner.labels).map((column) => inRows(column, (row: Row) => row.app)) : []),
    {
      id: "deploy",
      header: t("apps.table.columnLastDeploy"),
      width: "w-40",
      cell: (row) => (row.deploy ? <DeployMoment deploy={row.deploy} /> : <EmptyCell reason={t("apps.table.noRecentDeploy")} />),
      sortValue: (row) => {
        const moment = row.deploy ? parseTimestamp(deployMoment(row.deploy)) : null;
        return moment === null ? null : -moment.getTime();
      },
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      caption={caption}
      loading={loading}
      {...(skeletonRows !== undefined ? { skeletonRows } : {})}
      {...(empty !== undefined ? { empty } : {})}
      {...(rowActions ? { rowActions: (row: Row) => rowActions(row.app) } : {})}
      getRowId={(row) => (isFleetApp(row.app) ? `${row.app.node}:${row.app.domain}` : row.app.domain)}
      defaultSort={{ column: "domain", direction: "ascending" }}
      pagination="apps"
      {...(embedded ? { embedded: true } : {})}
      {...(className !== undefined ? { className } : {})}
    />
  );
}
