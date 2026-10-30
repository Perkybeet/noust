import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { isApiError } from "../../api/client";
import { auditPagesQuery } from "../../api/queries/audit";
import { sessionQuery } from "../../api/queries/auth";
import { jobsQuery } from "../../api/queries/jobs";
import { queueBusy, queueQuery } from "../../api/queries/queue";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { ServerScope } from "../operation/ServerScope";
import { ViewNotices } from "../operation/ViewNotices";
import { useView } from "../operation/views";
import type { ViewAnswer } from "../operation/views";
import { originOf } from "../fleet/data";
import { QueuePanelView } from "../queue/QueuePanel";
import { ActivityTable } from "./ActivityTable";
import { JobLogDrawer } from "./JobLogDrawer";
import { actionWords, actorWords, auditCategoriesFor, fleetTimeline, inKind, matchesText, mergeActivity, peopleIn, resultOptions } from "./data";
import type { ActivityJob, ActivityRow, AuditEntry } from "./data";
import { AUDIT_RESULTS, JOB_STATUSES, isFiltered, resultValidFor } from "./search";
import type { ActivitySearch } from "./search";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import type { NodeOutcome } from "../fleet/data";

const ALL = "all";
const EVERYONE = "@everyone";
/** How many more jobs a "Load more" click asks for; the jobs endpoint has no cursor, only a limit. */
const JOBS_PAGE = 50;
/** Entries per page of the audit log's own keyset cursor. */
const AUDIT_PAGE = 30;
/** Events asked of each server when every server is read at once, and how many more "Load more" adds. */
const FLEET_PAGE = 100;

export interface ActivityPageProps {
  search: ActivitySearch;
  onSearchChange: (search: ActivitySearch, options?: { replace?: boolean }) => void;
  /** `fleet` reads every server's events through the central; `server` is the server on screen, with its jobs. */
  scope?: "server" | "fleet";
}

/** A change to the filters: a key set to undefined is cleared. */
type SearchPatch = { [K in keyof ActivitySearch]?: ActivitySearch[K] | undefined };

/** What a source of rows hands to the page that draws them, whichever server or servers it read. */
interface Source {
  /** Every row the source holds, newest first, before the view and the words narrow them. */
  rows: readonly ActivityRow[];
  /** The first answer is on its way. */
  loading: boolean;
  /** Every part of it has answered, so an empty answer is a fact. */
  loaded: boolean;
  /** Nothing could be read at all: the page shows this instead of a table. */
  failure: ReactNode;
  /** More can be read behind "Load more". */
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  /** Said above the filters: the queue, a refusal, a server that did not answer. */
  notices: ReactNode[];
  /** Set for a list of several servers: the answer that says which they are, for the server filter. */
  fleet?: { view: ViewAnswer | undefined };
  /** How each server answered, for the server column of a list of several. */
  servers?: ReadonlyMap<string, NodeOutcome>;
  /** A job's log opens in a drawer, drawn once with the page. */
  openLog?: (job: ActivityJob) => void;
}

/** The page, whichever source feeds it: header, filters, the timeline and its footer. */
function ActivityView({ search, onSearchChange, source }: { search: ActivitySearch; onSearchChange: ActivityPageProps["onSearchChange"]; source: Source }) {
  const t = useT();
  const { data: reads } = useQuery({ ...sessionQuery(), select: (session) => session.permissions?.includes("audit.read") ?? false });
  const people = useMemo(() => peopleIn(source.rows), [source.rows]);
  const rows = useMemo(
    () => source.rows.filter((row) => inKind(row, search.kind) && matchesText(row, search.q ?? "", [actionWords(t, row).label, actorWords(t, row).label])),
    [source.rows, search.kind, search.q, t],
  );
  const filtered = isFiltered(search);
  const onlyView = search.result === undefined && search.q === undefined && search.actor === undefined && search.person === undefined && search.server === undefined;
  const nothingYet = source.loaded && !filtered && source.rows.length === 0;
  // Before either source has answered, neither is complete, so the merge reports "more"; a
  // "Load more" drawn under the skeleton would then be pushed down as the rows arrive.
  const hasMore = source.loaded && source.hasMore;

  const set = (patch: SearchPatch): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: ActivitySearch = {};
    if (next.kind) clean.kind = next.kind;
    if (next.result !== undefined && resultValidFor(next.result, clean.kind)) clean.result = next.result;
    if (next.q) clean.q = next.q;
    if (next.actor) clean.actor = next.actor;
    if (next.person) clean.person = next.person;
    if (next.server) clean.server = next.server;
    onSearchChange(clean, { replace: true });
  };
  const reset = (): void => {
    onSearchChange({ ...(search.kind !== undefined ? { kind: search.kind } : {}) });
  };

  let content;
  if (source.failure !== null) {
    content = source.failure;
  } else if (nothingYet) {
    content = (
      <EmptyState variant="firstUse" icon={<OBJECT_ICONS.activity />} title={t("activity.emptyTitle")} description={t("activity.emptyDescription")} command="noust audit list" />
    );
  } else {
    const view = search.kind ?? "changes";
    content = (
      <div className="flex flex-col gap-4">
        <ActivityTable
          rows={rows}
          caption={filtered ? t("activity.captionFiltered") : t("activity.caption")}
          loading={source.loading}
          onOpenJobLog={source.openLog ?? (() => undefined)}
          {...(source.servers !== undefined ? { servers: source.servers } : {})}
          empty={
            // Only the view chosen, and nothing in it: said as a fact, with nothing to clear.
            onlyView ? (
              <EmptyState variant="inline" title={t(`activity.noYet.${view}`)} />
            ) : (
              <EmptyState
                variant="inline"
                title={hasMore ? t("activity.noMatchYet") : t("activity.noMatch")}
                action={
                  <Button size="sm" variant="ghost" onClick={reset}>
                    {t("common.words.clearFilters")}
                  </Button>
                }
              />
            )
          }
        />
        {hasMore ? (
          <div>
            <Button size="sm" loading={source.loadingMore} onClick={source.loadMore}>
              {t("activity.loadMore")}
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <ListPage
      header={{
        title: t("activity.title"),
        description: t("activity.description"),
        // The trail itself (is it intact, who reviewed it, its export) is the audit log's, which Settings keeps.
        ...(reads === true
          ? {
              secondaryActions: (
                <Link to="/settings/audit" className={buttonClassName("secondary", "md")}>
                  <OBJECT_ICONS.audit aria-hidden="true" className="size-icon-md" />
                  {t("activity.auditLink")}
                </Link>
              ),
            }
          : {}),
      }}
      {...(source.notices.length > 0 ? { notice: <div className="flex flex-col gap-2">{source.notices}</div> } : {})}
      {...(nothingYet
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("activity.filterLabel")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => set({ q: value === "" ? undefined : value }),
                  label: t("activity.searchLabel"),
                  placeholder: t("activity.searchPlaceholder"),
                }}
                filters={
                  <>
                    <SegmentedControl<string>
                      label={t("activity.kindLabel")}
                      value={search.kind ?? "changes"}
                      onValueChange={(value) => set({ kind: value === "deployments" || value === "access" || value === "all" ? value : undefined })}
                      options={[
                        { value: "changes", label: t("activity.kindChanges") },
                        { value: "deployments", label: t("activity.kindDeployments") },
                        { value: "access", label: t("activity.kindAccess") },
                        { value: "all", label: t("activity.kindEverything") },
                      ]}
                    />
                    {source.fleet !== undefined ? <ServerScope view={source.fleet.view} value={search.server} onChange={(server) => set({ server })} /> : null}
                    {people.length > 0 || search.person !== undefined ? (
                      <Select
                        aria-label={t("activity.personLabel")}
                        value={search.person ?? EVERYONE}
                        onValueChange={(value) => set({ person: value === EVERYONE ? undefined : value })}
                        options={[
                          { value: EVERYONE, label: t("activity.personEveryone") },
                          ...(search.person !== undefined && !people.includes(search.person) ? [search.person, ...people] : people).map((name) => ({ value: name, label: name })),
                        ]}
                        className="min-w-40"
                      />
                    ) : null}
                    <Select
                      aria-label={t("activity.resultLabel")}
                      value={search.result ?? ALL}
                      onValueChange={(value) => set({ result: value === ALL ? undefined : value })}
                      options={[{ value: ALL, label: t("activity.resultEvery") }, ...resultOptions(t, search.kind)]}
                    />
                  </>
                }
                {...(onlyView
                  ? {}
                  : {
                      actions: (
                        <Button size="sm" variant="ghost" onClick={reset}>
                          {t("common.words.clearFilters")}
                        </Button>
                      ),
                    })}
              />
            ),
          })}
      {...(nothingYet ? {} : { footer: <CommandHint command={source.fleet === undefined ? "noust audit list" : "noust fleet activity"} label={t("common.words.fromTerminal")} /> })}
    >
      {content}
    </ListPage>
  );
}

/**
 * What happened on this server and who did it (T1): every job Noust ran and every action the
 * audit log recorded, merged into one newest-first timeline. It opens on every change made;
 * only the deployments, and sign-ins and access, are one click away. The jobs running now lead
 * the page; a job row opens its captured log. A session that may not read the audit log sees
 * jobs only, with a quiet note rather than an error.
 */
function ServerActivity({ search, onSearchChange }: Omit<ActivityPageProps, "scope">) {
  const t = useT();
  const [jobsLimit, setJobsLimit] = useState(JOBS_PAGE);
  const [openJob, setOpenJob] = useState<ActivityJob | null>(null);

  const wantsJobs = search.kind !== "access";
  const resultAppliesToJobs = search.result === undefined || JOB_STATUSES.has(search.result);
  const resultAppliesToAudit = search.result === undefined || AUDIT_RESULTS.has(search.result);
  // A result of the other vocabulary (a job status) matches no audit entry, and the other way
  // round: that source is left out rather than fetched unfiltered.
  const includeJobsQuery = wantsJobs && resultAppliesToJobs;
  const includeAuditQuery = resultAppliesToAudit;
  const jobStatus = includeJobsQuery && search.result !== undefined ? search.result : undefined;
  const auditResultFilter = includeAuditQuery && search.result !== undefined ? search.result : undefined;
  const auditCategories = auditCategoriesFor(search.kind);

  // A filter that changes what jobs mean starts "Load more" over; the audit log's own
  // infinite query already restarts on a query-key change, jobs' flat limit does not. Reset
  // during render (React's documented way to react to a derived-value change) rather than in
  // an effect, so it takes effect before the query below fires with the old limit.
  const [resetFor, setResetFor] = useState(jobStatus);
  if (resetFor !== jobStatus) {
    setResetFor(jobStatus);
    setJobsLimit(JOBS_PAGE);
  }

  const jobs = useQuery({
    ...jobsQuery({ limit: jobsLimit, ...(jobStatus !== undefined ? { status: jobStatus } : {}) }),
    enabled: includeJobsQuery,
  });
  const audit = useInfiniteQuery({
    ...auditPagesQuery({
      limit: AUDIT_PAGE,
      ...(auditCategories !== undefined ? { categories: auditCategories } : {}),
      ...(auditResultFilter !== undefined ? { result: auditResultFilter } : {}),
    }),
    enabled: includeAuditQuery,
  });

  const queue = useQuery(queueQuery());

  const auditForbidden = includeAuditQuery && audit.isError && isApiError(audit.error) && audit.error.status === 403;
  const includeAudit = includeAuditQuery && !auditForbidden;

  const allJobs = useMemo(() => jobs.data?.jobs ?? [], [jobs.data]);
  const jobsComplete = !includeJobsQuery || (jobs.data !== undefined && allJobs.length >= jobs.data.total);

  const allEntries = useMemo(() => audit.data?.pages.flatMap((page) => page.items) ?? [], [audit.data]);
  const auditComplete = !includeAudit || (audit.data !== undefined && (audit.data.pages.at(-1)?.next_before ?? null) === null);

  const merged = useMemo(
    () =>
      mergeActivity({
        jobs: includeJobsQuery ? allJobs : [],
        jobsComplete,
        entries: includeAudit ? allEntries : [],
        auditComplete,
        actor: search.actor,
        person: search.person,
      }),
    [includeJobsQuery, allJobs, jobsComplete, includeAudit, allEntries, auditComplete, search.actor, search.person],
  );

  const jobsHardError = includeJobsQuery && jobs.isError && jobs.data === undefined;
  const auditHardError = includeAuditQuery && !auditForbidden && audit.isError && audit.data === undefined;
  const loaded = (!includeJobsQuery || jobs.data !== undefined) && (!includeAuditQuery || auditForbidden || audit.data !== undefined);

  // What builds and what waits leads the page, with its place in the line and a way to cancel
  // it, drawn only when there is something in it so the page does not hold a gap open; at most
  // one other note, about an audit log this session may not read.
  const queueShown = queueBusy(queue.data) || queue.isError;
  const notices = [
    ...(queueShown ? [<QueuePanelView key="queue" queue={queue} onOpenLog={setOpenJob} />] : []),
    ...(auditForbidden ? [<Notice key="forbidden">{t("activity.auditForbidden")}</Notice>] : []),
  ];

  const failing = jobsHardError ? jobs : audit;
  const source: Source = {
    rows: merged.rows,
    loading: (includeJobsQuery && jobs.isPending) || (includeAuditQuery && audit.isPending),
    loaded,
    failure:
      jobsHardError || auditHardError ? (
        <ErrorBlock
          error={failing.error}
          title={t("activity.couldNotLoad")}
          onRetry={() => {
            if (jobsHardError) void jobs.refetch();
            if (auditHardError) void audit.refetch();
          }}
          retrying={(jobsHardError && jobs.isRefetching) || (auditHardError && audit.isRefetching)}
        />
      ) : null,
    hasMore: merged.hasMore,
    loadingMore: (includeJobsQuery && !jobsComplete && jobs.isFetching) || (includeAudit && audit.isFetchingNextPage),
    loadMore: () => {
      if (includeJobsQuery && !jobsComplete) setJobsLimit((limit) => limit + JOBS_PAGE);
      if (includeAudit && !auditComplete) void audit.fetchNextPage();
    },
    notices,
    openLog: setOpenJob,
  };

  return (
    <>
      <ActivityView search={search} onSearchChange={onSearchChange} source={source} />
      <JobLogDrawer job={openJob} onOpenChange={(open) => !open && setOpenJob(null)} />
    </>
  );
}

/**
 * What happened on every server (T1), the events each server's own audit log lets the operator read,
 * newest first, each named with its server. A server that does not let them is said in one place,
 * grouped with the others that refused the same thing, never as the JSON it answered with; the
 * server filter narrows the list and the person filter says what one person did, wherever they did it.
 */
function FleetActivity({ search, onSearchChange }: Omit<ActivityPageProps, "scope">) {
  const t = useT();
  const [limit, setLimit] = useState(FLEET_PAGE);
  const view = useView("activity", { nodes: search.server !== undefined ? [search.server] : [], limit });
  const items = useMemo(() => (view.data?.items ?? []).map((row) => ({ entry: row as unknown as AuditEntry, origin: originOf(row) })), [view.data]);
  const rows = useMemo(() => fleetTimeline(items, search.person, search.actor), [items, search.person, search.actor]);
  // A server that gave as many events as were asked of it may have more behind them.
  const more = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) counts.set(item.origin.node, (counts.get(item.origin.node) ?? 0) + 1);
    return [...counts.values()].some((count) => count >= limit);
  }, [items, limit]);

  const source: Source = {
    rows,
    loading: view.isPending,
    loaded: view.data !== undefined,
    failure:
      // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
      view.isError && view.data === undefined ? (
        <ErrorBlock error={view.error} title={t("activity.couldNotLoad")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      ) : null,
    hasMore: more,
    loadingMore: view.isFetching && view.data !== undefined,
    loadMore: () => {
      setLimit((current) => current + FLEET_PAGE);
    },
    notices: [<ViewNotices key="notices" view={view.data} resource="activity" />],
    fleet: { view: view.data },
    // Only a list of several servers names the server of each event.
    ...(view.data?.fleet === true ? { servers: view.outcomes } : {}),
  };
  return <ActivityView search={search} onSearchChange={onSearchChange} source={source} />;
}

export function ActivityPage({ search, onSearchChange, scope = "server" }: ActivityPageProps) {
  return scope === "fleet" ? <FleetActivity search={search} onSearchChange={onSearchChange} /> : <ServerActivity search={search} onSearchChange={onSearchChange} />;
}
