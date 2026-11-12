import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Play, RotateCcw, Settings2 } from "lucide-react";
import { useMemo, useState } from "react";

import { centralCopiesQuery, collectingStatusQuery } from "../../../api/queries/centralBackups";
import type { CentralCopy, CollectingNode } from "../../../api/queries/centralBackups";
import { FilterBar } from "../../../components/page/FilterBar";
import { ListPage } from "../../../components/page/ListPage";
import { GuardedButton, ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Section } from "../../../components/page/Section";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { matchesQuery } from "../../fleet/filters";
import { CollectSettingsDrawer } from "./CollectSettingsDrawer";
import { GiveBackDialog } from "./GiveBackDialog";
import { copyKey, followChoice, followValue, isKind, knobsOf, movingOf, nodeView, roomOf } from "./central";
import type { FollowChoice } from "./central";
import { DeleteCopyDialog } from "./DeleteCopyDialog";
import { useCentralBackupActions } from "./useCentralBackupActions";

const FOLLOW_LABEL = {
  fleet: "centralBackups.servers.follow.fleet",
  on: "centralBackups.servers.follow.on",
  off: "centralBackups.servers.follow.off",
} as const;

function copyColumns(t: T): Column<CentralCopy>[] {
  return [
    {
      id: "of",
      header: t("centralBackups.copies.column.of"),
      card: "title",
      sortValue: (row) => row.owner,
      cell: (row) => (
        <div className="flex min-w-0 flex-col">
          <span translate="no" className="truncate font-medium">
            {row.owner}
          </span>
          {row.origin === "scheduled" || row.origin === "manual" || row.origin === "safety" ? <span className="text-12 text-fg-muted">{t(`centralBackups.copies.origin.${row.origin}`)}</span> : null}
        </div>
      ),
    },
    {
      id: "kind",
      header: t("centralBackups.copies.column.kind"),
      hideBelow: "sm",
      card: "meta",
      sortValue: (row) => row.kind,
      cell: (row) => (isKind(row.kind) ? t(`centralBackups.copies.kind.${row.kind}`) : row.kind),
    },
    {
      id: "server",
      header: t("centralBackups.copies.column.server"),
      card: "meta",
      sortValue: (row) => row.node,
      cell: (row) => <Mono>{row.node}</Mono>,
    },
    {
      id: "taken",
      header: t("centralBackups.copies.column.taken"),
      card: "meta",
      sortValue: (row) => row.created_at,
      cell: (row) => <RelativeTime value={row.created_at} className="text-13 text-fg" />,
    },
    {
      id: "size",
      header: t("centralBackups.copies.column.size"),
      align: "end",
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => row.size,
      cell: (row) => <span className="text-13 text-fg tabular-nums">{formatBytes(row.size, t.locale)}</span>,
    },
    {
      id: "checked",
      header: t("centralBackups.copies.column.checked"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) =>
        row.verified === "digest" || row.verified === "size" ? (
          <span className="text-13 text-fg-muted">{t(`centralBackups.copies.verified.${row.verified}`)}</span>
        ) : (
          <EmptyCell reason={row.verified} />
        ),
    },
  ];
}

function serverColumns(t: T): Column<CollectingNode>[] {
  return [
    {
      id: "server",
      header: t("centralBackups.servers.column.server"),
      card: "title",
      sortValue: (row) => row.node,
      cell: (row) => <Mono className="font-medium">{row.node}</Mono>,
    },
    {
      id: "state",
      header: t("centralBackups.servers.column.state"),
      card: "status",
      cell: (row) => {
        const view = nodeView(row);
        const moving = movingOf(row);
        const hint =
          view.hint !== undefined
            ? t(view.hint)
            : moving !== null && moving.size > 0
              ? t("centralBackups.servers.hint.moving", { done: formatBytes(moving.done, t.locale), size: formatBytes(moving.size, t.locale) })
              : null;
        return (
          <div className="flex min-w-0 flex-col gap-1">
            <StatusPill state={view.status} label={view.moving !== undefined ? t("centralBackups.servers.state.moving", { item: view.moving }) : t(view.label ?? "centralBackups.servers.state.failed")} appearance="inline" size="sm" />
            {hint !== null ? <p className="max-w-measure-help text-12 text-pretty text-fg-muted">{hint}</p> : null}
            {/* The server's own words, verbatim: what failed is never paraphrased. */}
            {view.status === "failed" && (row.last_error ?? "") !== "" ? (
              <p className="max-w-measure-help text-12 break-words text-fg-muted">
                <Mono tone="default">{row.last_error}</Mono>
              </p>
            ) : null}
          </div>
        );
      },
    },
    {
      id: "last",
      header: t("centralBackups.servers.column.last"),
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => row.last_pass_at ?? "",
      cell: (row) => <RelativeTime value={row.last_pass_at} fallback={t("centralBackups.servers.never")} className="text-13 text-fg" />,
    },
    {
      id: "held",
      header: t("centralBackups.servers.column.held"),
      hideBelow: "lg",
      card: "meta",
      sortValue: (row) => row.held["bytes"] ?? 0,
      cell: (row) => {
        const count = row.held["count"] ?? 0;
        return count === 0 ? <span className="text-13 text-fg-muted">{t("centralBackups.servers.heldNone")}</span> : <span className="text-13 text-fg tabular-nums">{t("centralBackups.servers.held", { count, size: formatBytes(row.held["bytes"] ?? 0, t.locale) })}</span>;
      },
    },
    {
      id: "follow",
      header: t("centralBackups.servers.column.follow"),
      hideBelow: "sm",
      card: "meta",
      cell: (row) => <span className="text-13 text-fg">{t(FOLLOW_LABEL[followChoice(row)])}</span>,
    },
  ];
}

/**
 * The copies a central holds of its servers, and how collecting is going with each one. The central comes for the copies
 * through the tunnel it already has (nothing is opened on the router, nothing on a server can delete them), and this is
 * where they are: which server's, when it was made, whether what arrived matched, and what to do with it: restore it on
 * the server it came from or on another, or let it go. A server that cannot be collected from says why in its own row, in
 * the server's words, with the fix above them.
 */
export function CentralCopiesPage() {
  const t = useT();
  const canManage = useCan("backups.manage");
  const canRun = useCan("backups.run");
  const status = useQuery(collectingStatusQuery());
  const copies = useQuery(centralCopiesQuery());
  const { collect, follow } = useCentralBackupActions();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [restoring, setRestoring] = useState<CentralCopy | null>(null);
  const [deleting, setDeleting] = useState<CentralCopy | null>(null);
  const [query, setQuery] = useState("");

  const knobs = knobsOf(status.data);
  const room = roomOf(status.data);
  const nodes = useMemo(() => status.data?.nodes ?? [], [status.data]);
  const servers = useMemo(() => nodes.map((node) => node.node), [nodes]);
  const rows = useMemo(() => copies.data?.items ?? [], [copies.data]);
  const shown = rows.filter((row) => matchesQuery([row.owner, row.node, row.kind], query || undefined));
  const nobodyFollowed = status.data?.following.following === 0;

  const tableColumns = useMemo(() => copyColumns(t), [t]);
  const stateColumns = useMemo(() => serverColumns(t), [t]);

  const roomLine = room === null ? null : t("centralBackups.space.held", { size: formatBytes(room.used, t.locale), free: formatBytes(room.free, t.locale) });
  const following = status.data?.following;

  const notices = (
    <div className="flex flex-col gap-3">
      {room?.low === true ? (
        <Notice tone="warning" title={t("centralBackups.notice.lowTitle")}>
          {t("centralBackups.notice.lowBody")}
        </Notice>
      ) : null}
      {!nobodyFollowed && status.data !== undefined ? (
        <Notice
          title={t("centralBackups.notice.oneCopyTitle")}
          action={
            <Link to="/backups/destinations" className={buttonClassName("secondary", "sm")}>
              {t("centralBackups.notice.oneCopyAction")}
            </Link>
          }
        >
          {t("centralBackups.notice.oneCopyBody")}
        </Notice>
      ) : null}
    </div>
  );

  let copiesContent;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (copies.isError && copies.data === undefined) {
    copiesContent = <ErrorBlock error={copies.error} title={t("centralBackups.page.loadFailed")} onRetry={() => void copies.refetch()} retrying={copies.isRefetching} />;
  } else if (copies.data !== undefined && rows.length === 0) {
    copiesContent = (
      <EmptyState
        variant="firstUse"
        icon={<OBJECT_ICONS.backup />}
        title={t("centralBackups.copies.empty")}
        description={nobodyFollowed ? t("centralBackups.copies.emptyOff") : t("centralBackups.copies.emptyDescription")}
        {...(nobodyFollowed && canManage
          ? {
              action: (
                <Button
                  onClick={() => {
                    setSettingsOpen(true);
                  }}
                >
                  {t("centralBackups.notice.offAction")}
                </Button>
              ),
            }
          : {})}
      />
    );
  } else {
    copiesContent = (
      <DataTable
        caption={t("centralBackups.copies.caption")}
        columns={tableColumns}
        rows={shown}
        getRowId={copyKey}
        loading={copies.isPending}
        skeletonRows={4}
        pagination="central-copies"
        defaultSort={{ column: "taken", direction: "descending" }}
        {...(canManage || canRun
          ? {
              rowActions: (row: CentralCopy) => (
                <RowMenu name={row.owner}>
                  {canManage ? (
                    <MenuItem
                      icon={<RotateCcw />}
                      onClick={() => {
                        setRestoring(row);
                      }}
                    >
                      {t("centralBackups.action.restore")}
                    </MenuItem>
                  ) : null}
                  {canManage ? <MenuSeparator /> : null}
                  {canManage ? (
                    <MenuItem
                      icon={<ICONS.delete />}
                      destructive
                      onClick={() => {
                        setDeleting(row);
                      }}
                    >
                      {t("centralBackups.action.delete")}
                    </MenuItem>
                  ) : null}
                </RowMenu>
              ),
            }
          : {})}
        empty={<EmptyState variant="inline" title={t("centralBackups.copies.noMatch")} />}
      />
    );
  }

  return (
    <ListPage
      header={{
        title: t("centralBackups.page.title"),
        description: t("centralBackups.page.description"),
        ...(roomLine !== null
          ? {
              meta: (
                <span className="flex items-center gap-1.5">
                  <OBJECT_ICONS.disk aria-hidden="true" className="size-icon-sm text-fg-faint" />
                  <span className="tabular-nums">{roomLine}</span>
                </span>
              ),
            }
          : {}),
        ...(canManage
          ? {
              secondaryActions: (
                <Button
                  icon={<Settings2 aria-hidden="true" />}
                  onClick={() => {
                    setSettingsOpen(true);
                  }}
                >
                  {t("centralBackups.action.settings")}
                </Button>
              ),
            }
          : {}),
        primaryAction: (
          <GuardedButton
            permission="backups.run"
            variant="primary"
            icon={<Play aria-hidden="true" />}
            loading={collect.isPending}
            onClick={() => {
              collect.mutate(null);
            }}
          >
            {t("centralBackups.action.collect")}
          </GuardedButton>
        ),
      }}
      notice={notices}
    >
      <ReadOnlyLine permission="backups.manage" />
      <Section
        title={t("centralBackups.servers.title")}
        description={
          following === undefined
            ? undefined
            : following.following === 0
              ? t("centralBackups.following.none")
              : following.following === following.of && (following.exceptions ?? []).length === 0
                ? t("centralBackups.following.all")
                : t("centralBackups.following.some", { following: following.following, of: following.of })
        }
      >
        {/* design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed */}
        {status.isError && status.data === undefined ? (
          <ErrorBlock error={status.error} title={t("centralBackups.page.loadFailed")} onRetry={() => void status.refetch()} retrying={status.isRefetching} />
        ) : status.data !== undefined && nodes.length === 0 ? (
          <EmptyState variant="inline" title={t("centralBackups.servers.empty")} description={t("centralBackups.servers.emptyHint")} />
        ) : (
          <DataTable
            caption={t("centralBackups.servers.caption")}
            columns={stateColumns}
            rows={nodes}
            getRowId={(row) => row.node}
            loading={status.isPending}
            skeletonRows={3}
            defaultSort={{ column: "server", direction: "ascending" }}
            {...(canManage || canRun
              ? {
                  rowActions: (row: CollectingNode) => (
                    <RowMenu name={row.node}>
                      {canRun ? (
                        <MenuItem
                          icon={<Play />}
                          disabled={!row.following}
                          onClick={() => {
                            collect.mutate(row.node);
                          }}
                        >
                          {t("centralBackups.servers.menu.collect")}
                        </MenuItem>
                      ) : null}
                      {canManage ? <MenuSeparator /> : null}
                      {canManage
                        ? (["fleet", "on", "off"] as const).map((choice: FollowChoice) => (
                            <MenuItem
                              key={choice}
                              disabled={followChoice(row) === choice || follow.isPending}
                              onClick={() => {
                                follow.mutate({ node: row.node, follow: followValue(choice) });
                              }}
                            >
                              {t(`centralBackups.servers.menu.${choice}`)}
                            </MenuItem>
                          ))
                        : null}
                    </RowMenu>
                  ),
                }
              : {})}
          />
        )}
      </Section>

      <Section
        title={t("centralBackups.copies.title")}
        {...(status.data?.directory != null ? { description: t.rich("centralBackups.space.keptIn", { path: <Mono tone="default">{status.data.directory}</Mono> }) } : {})}
        {...(copies.data !== undefined ? { badge: <span className="text-13 text-fg-muted tabular-nums">{t("centralBackups.copies.count", { count: rows.length })}</span> } : {})}
      >
        <FilterBar
          label={t("centralBackups.copies.searchLabel")}
          search={{ value: query, onChange: setQuery, label: t("centralBackups.copies.searchLabel"), placeholder: t("centralBackups.copies.searchPlaceholder") }}
        />
        {copiesContent}
      </Section>

      {settingsOpen ? <CollectSettingsDrawer knobs={knobs} open onOpenChange={setSettingsOpen} /> : null}
      {restoring !== null ? (
        <GiveBackDialog
          key={copyKey(restoring)}
          copy={restoring}
          servers={servers}
          open
          onOpenChange={(open) => {
            if (!open) setRestoring(null);
          }}
        />
      ) : null}
      {deleting !== null ? (
        <DeleteCopyDialog
          copy={deleting}
          open
          onOpenChange={(open) => {
            if (!open) setDeleting(null);
          }}
        />
      ) : null}
    </ListPage>
  );
}
