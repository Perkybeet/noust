import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ChevronDown, Copy, GitCommitHorizontal, Hammer, History, PackageCheck, Rocket, RotateCw, ScrollText, X } from "lucide-react";
import { useMemo, useState } from "react";

import { appQuery, releasesQuery } from "../../../api/queries/apps";
import type { Release } from "../../../api/queries/apps";
import { deploymentPagesQuery } from "../../../api/queries/deployments";
import { rolloutQuery } from "../../../api/queries/rollout";
import { useDocumentTitle } from "../../../app/documentTitle";
import { DeployStatePill } from "../../../components/page/AppStatePill";
import { useNow } from "../../../components/page/clock";
import { FilterBar } from "../../../components/page/FilterBar";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { MessageKey, T } from "../../../i18n";
import { copyText } from "../../../lib/clipboard";
import { formatDuration, parseTimestamp } from "../../../lib/format";
import type { Deployment } from "../../apps/data";
import { reportActionError, useAppActions } from "../../apps/useAppActions";
import { QueuePanel } from "../../queue/QueuePanel";
import { DeployHold } from "../DeployHold";
import { useRollback } from "../RollbackHost";
import { keptServing, servingDeployment } from "../rollback";
import { BackupPoints } from "./BackupPoints";
import { RebuildDialog, useDeploymentAction, useRebuild } from "./DeploymentActions";
import { usePromoteBuild } from "./PromoteBuild";
import { DEPLOY_STATE_FILTERS, STARTED_BY_FILTERS, branchesOf, filterDeployments, isFiltered } from "./filters";
import type { DeploymentsSearch } from "./filters";
import { deploymentStatusWord, shortCommit, triggerWords } from "./words";

/** Rows asked for at once: far more than the store keeps (twenty by default), so the history is whole and filters see all of it. */
const HISTORY_PAGE = 200;

/** The words of the states a deployment can be filtered by: the ones every deployment pill already says. */
const STATE_WORDS = {
  live: "appPages.deployments.words.live",
  running: "common.deployState.inProgress",
  succeeded: "common.deployState.succeeded",
  failed: "common.deployState.failed",
  rolled_back: "common.deployState.rolledBack",
  cancelled: "common.deployState.cancelled",
} as const satisfies Record<(typeof DEPLOY_STATE_FILTERS)[number], MessageKey>;

const RUNNING = new Set(["queued", "running"]);
const ALL = "all";

/** What a row of the list is: a deployment, or a build that waits to be made live. */
type Row =
  | { kind: "deployment"; deployment: Deployment; live: boolean; removed: boolean; kept: number | null }
  | { kind: "built"; release: Release };

/** How long a deploy took, or has been running for. */
function Duration({ deployment, t }: { deployment: Deployment; t: T }) {
  const running = RUNNING.has(deployment.status);
  const started = parseTimestamp(deployment.started_at);
  const now = useNow(() => (running ? 1_000 : 3_600_000));
  if (running) {
    if (started === null) return <EmptyCell reason={t("appPages.deployments.tab.notStarted")} />;
    return <span className="text-fg-muted">{formatDuration(Math.max(0, (now - started.getTime()) / 1000), t.locale)}</span>;
  }
  if (deployment.duration_s === null || deployment.duration_s === undefined) return <EmptyCell reason={t("appPages.common.notRecorded")} />;
  return <>{formatDuration(deployment.duration_s, t.locale)}</>;
}

function columns(t: T): Column<Row>[] {
  return [
    {
      id: "id",
      header: t("appPages.deployments.tab.deployColumn"),
      width: "w-20",
      // The table makes this cell the row's button (a row opens on press): a link inside it would be a control in a control.
      cell: (row) =>
        row.kind === "built" ? (
          <EmptyCell reason={t("appPages.deployments.tab.notDeployedYet")} />
        ) : (
          <>
            <span className="sr-only">{t("appPages.deployments.page.heading", { id: String(row.deployment.id) })}</span>
            <span aria-hidden="true" className="tabular-nums">{`#${String(row.deployment.id)}`}</span>
          </>
        ),
    },
    {
      id: "status",
      header: t("appPages.deployments.tab.statusColumn"),
      width: "w-40",
      card: "status",
      cell: (row) =>
        row.kind === "built" ? (
          // Built and never served: still, like the other states that wait for somebody.
          <StatusPill state="queued" label={t("rollout.releases.waiting")} appearance="inline" size="sm" />
        ) : row.live ? (
          <StatusPill state="running" label={t("appPages.deployments.words.live")} appearance="inline" size="sm" />
        ) : (
          <DeployStatePill status={deploymentStatusWord(row.deployment)} appearance="inline" size="sm" />
        ),
    },
    {
      id: "commit",
      header: t("appPages.deployments.tab.commitColumn"),
      cell: (row) => {
        const deployment = row.kind === "deployment" ? row.deployment : null;
        const commit = shortCommit(deployment?.git_commit ?? (row.kind === "built" ? row.release.commit : null));
        return (
          <span className="flex min-w-0 flex-col py-1.5">
            <span className="flex min-w-0 items-baseline gap-2">
              {commit ? <Mono>{commit}</Mono> : <span className="text-12 text-fg-faint">{t("appPages.deployments.tab.noCommit")}</span>}
              {deployment?.git_branch ? (
                <Mono tone="faint" className="hidden truncate sm:inline">
                  {deployment.git_branch}
                </Mono>
              ) : null}
              {deployment?.schema_changed ? <Badge className="shrink-0">{t("appPages.deployments.hooks.schemaChangedBadge")}</Badge> : null}
            </span>
            {deployment?.commit_message ? (
              <span title={deployment.commit_message} className="hidden max-w-96 truncate text-12 text-fg-faint md:block">
                {deployment.commit_message}
              </span>
            ) : null}
            {row.kind === "deployment" && row.deployment.status === "failed" && !row.deployment.cancelled && row.kept !== null ? (
              <span className="text-12 text-fg-muted">{t("appPages.deployments.words.keptServing", { id: String(row.kept) })}</span>
            ) : null}
            {row.kind === "deployment" && row.removed ? <span className="text-12 text-fg-muted">{t("appPages.deployments.releases.removedFromDisk")}</span> : null}
            {/* The live row's pill says Live; that it serves with a hook that failed is still said. */}
            {row.kind === "deployment" && row.live && row.deployment.warnings ? (
              <StatusPill state="warning" label={t("common.deployState.withWarnings")} appearance="inline" size="sm" />
            ) : null}
          </span>
        );
      },
    },
    {
      id: "trigger",
      header: t("appPages.deployments.fields.startedBy"),
      hideBelow: "md",
      card: "hidden",
      cell: (row) => {
        if (row.kind === "built") return <EmptyCell reason={t("appPages.deployments.tab.notDeployedYet")} />;
        const words = triggerWords(t, row.deployment.triggered_by);
        const Icon = words.icon;
        return (
          <span className="flex items-center gap-1.5 text-fg-muted">
            <Icon aria-hidden="true" className="size-icon-sm shrink-0" />
            {words.label}
          </span>
        );
      },
    },
    {
      id: "started",
      header: t("appPages.deployments.fields.started"),
      cell: (row) => <RelativeTime value={row.kind === "built" ? row.release.created_at : row.deployment.started_at} className="text-fg-muted" />,
    },
    {
      id: "duration",
      header: t("appPages.deployments.fields.duration"),
      align: "end",
      mono: true,
      hideBelow: "sm",
      cell: (row) => (row.kind === "built" ? <EmptyCell reason={t("appPages.common.notRecorded")} /> : <Duration deployment={row.deployment} t={t} />),
    },
  ];
}

/** A change to the filters: a key set to undefined is cleared. */
type SearchPatch = { [K in keyof DeploymentsSearch]?: DeploymentsSearch[K] | undefined };

export interface DeploymentsTabProps {
  domain: string;
  search: DeploymentsSearch;
  /**
   * Sets the filters in the URL. `replace` for typing, so the history does not gain an entry
   * per keystroke; choosing a filter pushes one, so Back undoes it.
   */
  onSearchChange: (search: DeploymentsSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every deploy of one app, newest first, in one list: what serves is marked Live, the failed ones
 * say which version kept serving, a build that waits to be made live sits at the top, and each
 * row opens its page or acts from its menu (the log, going back to it, building its commit
 * again). Search and filters are in the URL. What an app kept in a single folder can be restored
 * from is under the list.
 */
export function DeploymentsTab({ domain, search, onSearchChange }: DeploymentsTabProps) {
  const t = useT();
  useDocumentTitle(t("appPages.deployments.tab.documentTitle", { domain }), 1);
  const navigate = useNavigate();
  const app = useQuery(appQuery(domain));
  const pages = useInfiniteQuery(deploymentPagesQuery({ domain, limit: HISTORY_PAGE }));
  const releases = useQuery({ ...releasesQuery(domain), enabled: app.data?.layout === "releases" });
  const rollout = useQuery({ ...rolloutQuery(domain), retry: false });
  const { openRollback } = useRollback();
  const { update } = useAppActions(domain);
  const action = useDeploymentAction(domain, t);
  const retry = useRebuild(domain, (job) => action.follow("rebuild", job));
  const promote = usePromoteBuild(domain);
  const [rebuilding, setRebuilding] = useState<Deployment | null>(null);

  const history = useMemo(() => pages.data?.pages.flatMap((page) => page.items) ?? [], [pages.data]);
  const total = pages.data?.pages[0]?.total ?? 0;
  const live = servingDeployment(history)?.id ?? null;
  const onDisk = useMemo(() => new Set((releases.data?.items ?? []).filter((release) => release.on_disk).map((release) => release.id)), [releases.data]);
  const releaseLayout = app.data?.layout === "releases" && releases.data !== null && releases.data !== undefined;
  const filtered = isFiltered(search);

  const rows = useMemo<Row[]>(() => {
    const built: Row[] = filtered
      ? []
      : (releases.data?.items ?? []).filter((release) => release.waiting && !release.active && release.on_disk).map((release) => ({ kind: "built" as const, release }));
    const kept = filterDeployments(history, search, live).map<Row>((deployment) => ({
      kind: "deployment",
      deployment,
      live: deployment.id === live,
      // A release that was built, served and has since been pruned: there is nothing to go back to.
      removed: releaseLayout && deployment.release_id !== null && deployment.release_id !== undefined && deployment.status === "success" && !onDisk.has(deployment.release_id),
      kept: keptServing(history, deployment),
    }));
    return [...built, ...kept];
  }, [history, search, live, releases.data, filtered, releaseLayout, onDisk]);

  const set = (patch: SearchPatch, replace = false): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: DeploymentsSearch = {};
    if (next.q) clean.q = next.q;
    if (next.state) clean.state = next.state;
    if (next.branch) clean.branch = next.branch;
    if (next.by) clean.by = next.by;
    onSearchChange(clean, { replace });
  };

  const stateLabel = (state: (typeof DEPLOY_STATE_FILTERS)[number]): string => t(STATE_WORDS[state]);
  const branches = branchesOf(history);
  const keep = rollout.data?.history_keep_effective ?? 20;

  const rowActions = (row: Row) => {
    if (row.kind === "built") {
      return (
        <RowMenu name={t("rollout.releases.waiting")}>
          <MenuItem icon={<PackageCheck />} onClick={() => promote.ask(row.release)}>
            {t("rollout.releases.promote")}
          </MenuItem>
        </RowMenu>
      );
    }
    const deployment = row.deployment;
    const commit = shortCommit(deployment.git_commit);
    const failed = deployment.status === "failed" && !deployment.cancelled;
    const name = `#${String(deployment.id)}`;
    return (
      <RowMenu name={name}>
        <MenuItem icon={<ScrollText />} onClick={() => void navigate({ to: "/apps/$domain/deployments/$id", params: { domain, id: String(deployment.id) } })}>
          {t("appPages.common.viewLog")}
        </MenuItem>
        {deployment.rollback_available ? (
          <MenuItem icon={<History />} onClick={() => openRollback({ deployment: deployment.id })}>
            {t("appPages.deployments.tab.rollBackToThis")}
          </MenuItem>
        ) : null}
        {failed && commit !== null ? (
          <MenuItem
            icon={<RotateCw />}
            onClick={() => {
              action.dismiss();
              retry.mutate({ id: deployment.id }, { onError: (error) => reportActionError(t("appPages.deployments.actions.retryFailed"), error) });
            }}
          >
            {t("appPages.deployments.actions.retry")}
          </MenuItem>
        ) : null}
        {failed && commit === null ? (
          <MenuItem icon={<Hammer />} onClick={() => update.mutate()}>
            {t("appPages.actions.update")}
          </MenuItem>
        ) : null}
        {!failed && commit !== null ? (
          <MenuItem icon={<GitCommitHorizontal />} onClick={() => setRebuilding(deployment)}>
            {t("appPages.deployments.actions.rebuildThisCommit")}
          </MenuItem>
        ) : null}
        {commit !== null ? (
          <MenuItem
            icon={<Copy />}
            onClick={() => {
              void copyText(deployment.git_commit ?? commit).then(
                // design-exception: toast-success the clipboard is not on screen, so a toast is the only word that it was copied
                () => toast.success(t("appPages.deployments.tab.commitCopied", { commit })),
                (error: unknown) => reportActionError(t("appPages.deployments.tab.commitNotCopied"), error),
              );
            }}
          >
            {t("appPages.deployments.page.copyCommit")}
          </MenuItem>
        ) : null}
      </RowMenu>
    );
  };

  const empty =
    pages.data !== undefined && history.length === 0 && rows.length === 0 ? (
      <EmptyState
        variant="firstUse"
        level={2}
        icon={<Rocket />}
        title={t("appPages.deployments.tab.emptyTitle")}
        description={t("appPages.deployments.tab.emptyDescription")}
        command={`noust update ${domain}`}
      />
    ) : (
      <EmptyState
        variant="inline"
        title={t("appPages.deployments.tab.noMatch")}
        action={
          <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
            {t("appPages.deployments.tab.clearFilters")}
          </Button>
        }
      />
    );

  const firstUse = pages.data !== undefined && history.length === 0 && rows.length === 0;
  const count =
    pages.data === undefined ? "" : filtered ? t("appPages.deployments.tab.countFiltered", { shown: rows.length, total: history.length }) : t("appPages.deployments.tab.count", { count: history.length });

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {/* A freeze over the app or the pause of what deploys it by itself: nothing drawn when neither holds. */}
      <DeployHold domain={domain} />
      {/* What builds or waits for this app, with its place in the line: nothing drawn when it is empty. */}
      <QueuePanel domain={domain} />
      {action.failed !== null && action.kind === "rebuild" ? (
        <ErrorBlock live compact error={{ detail: action.failed.error ?? t("appPages.job.noReason") }} title={t("appPages.deployments.actions.rebuildFailed")} />
      ) : null}
      {pages.isError && pages.data === undefined ? (
        <ErrorBlock error={pages.error} title={t("appPages.deployments.tab.loadError")} onRetry={() => void pages.refetch()} retrying={pages.isRefetching} />
      ) : (
        <>
          {firstUse ? null : (
            <FilterBar
              label={t("appPages.deployments.tab.filterLabel")}
              search={{
                value: search.q ?? "",
                onChange: (value) => set({ q: value }, true),
                label: t("appPages.deployments.tab.searchLabel"),
                placeholder: t("appPages.deployments.tab.searchPlaceholder"),
              }}
              filters={
                <>
                  <Select
                    aria-label={t("appPages.deployments.tab.stateLabel")}
                    value={search.state ?? ALL}
                    onValueChange={(value) => set({ state: DEPLOY_STATE_FILTERS.find((state) => state === value) })}
                    options={[{ value: ALL, label: t("apps.page.everyState") }, ...DEPLOY_STATE_FILTERS.map((state) => ({ value: state, label: stateLabel(state) }))]}
                    className="min-w-36"
                  />
                  {branches.length > 1 || search.branch !== undefined ? (
                    <Select
                      aria-label={t("appPages.deployments.tab.branchLabel")}
                      value={search.branch ?? ALL}
                      onValueChange={(value) => set({ branch: value === ALL ? undefined : value })}
                      options={[{ value: ALL, label: t("appPages.deployments.tab.everyBranch") }, ...branches.map((branch) => ({ value: branch, label: branch }))]}
                      className="min-w-36"
                      mono
                    />
                  ) : null}
                  <Select
                    aria-label={t("appPages.deployments.tab.byLabel")}
                    value={search.by ?? ALL}
                    onValueChange={(value) => set({ by: STARTED_BY_FILTERS.find((by) => by === value) })}
                    options={[
                      { value: ALL, label: t("appPages.deployments.tab.everyone") },
                      ...STARTED_BY_FILTERS.map((by) => ({ value: by, label: triggerWords(t, by).label })),
                    ]}
                    className="min-w-36"
                  />
                </>
              }
              count={count}
              {...(filtered
                ? {
                    actions: (
                      <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                        {t("appPages.deployments.tab.clearFilters")}
                      </Button>
                    ),
                  }
                : {})}
            />
          )}
          {firstUse ? (
            empty
          ) : (
            <DataTable
              caption={t("appPages.deployments.tab.caption", { domain })}
              columns={columns(t)}
              rows={pages.data === undefined ? undefined : rows}
              getRowId={(row) => (row.kind === "built" ? `built-${row.release.id}` : String(row.deployment.id))}
              density="compact"
              mobile="cards"
              skeletonRows={6}
              pagination="deployments"
              rowActions={rowActions}
              onRowActivate={(row) => {
                // A build that waits has no page: pressing its row offers what it can do, to make it serve.
                if (row.kind === "built") promote.ask(row.release);
                else void navigate({ to: "/apps/$domain/deployments/$id", params: { domain, id: String(row.deployment.id) } });
              }}
              empty={empty}
            />
          )}
          {pages.isError ? (
            <ErrorBlock compact live error={pages.error} title={t("appPages.deployments.tab.olderLoadError")} onRetry={() => void pages.fetchNextPage()} />
          ) : null}
          {pages.hasNextPage ? (
            <div className="flex flex-wrap items-center gap-3">
              <Button size="sm" icon={<ChevronDown aria-hidden="true" />} loading={pages.isFetchingNextPage} onClick={() => void pages.fetchNextPage()}>
                {t("appPages.deployments.tab.loadOlder")}
              </Button>
              <span className="text-12 text-fg-muted">{t("appPages.deployments.tab.showing", { shown: history.length, total })}</span>
            </div>
          ) : !firstUse && pages.data !== undefined ? (
            <p className="text-12 text-fg-muted">{t("appPages.deployments.tab.keeps", { count: keep })}</p>
          ) : null}
        </>
      )}
      {app.data !== undefined && app.data.layout !== "releases" ? <BackupPoints domain={domain} /> : null}
      {rebuilding !== null && app.data !== undefined ? (
        <RebuildDialog
          domain={domain}
          deployment={rebuilding}
          layout={app.data.layout}
          open
          onOpenChange={(open) => {
            if (!open) setRebuilding(null);
          }}
          onQueued={(job) => action.follow("rebuild", job)}
          t={t}
        />
      ) : null}
      {promote.dialogs}
    </div>
  );
}
