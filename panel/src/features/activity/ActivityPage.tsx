import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { History, ScrollText } from "lucide-react";
import { useMemo, useState } from "react";

import { isApiError } from "../../api/client";
import { auditPagesQuery } from "../../api/queries/audit";
import { activeJobsQuery, jobsQuery } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { JobProgress } from "../../components/page/JobProgress";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { ActivityTable } from "./ActivityTable";
import { JobLogDrawer } from "./JobLogDrawer";
import { AUDIT_RESULTS, JOB_STATUSES, actionWords, actorWords, auditCategoriesFor, inKind, isFiltered, matchesText, mergeActivity, resultOptions, resultValidFor } from "./data";
import type { ActivityJob, ActivitySearch } from "./data";

const ALL = "all";
const OPERATIONS = "operations";
/** How many more jobs a "Load more" click asks for; the jobs endpoint has no cursor, only a limit. */
const JOBS_PAGE = 50;
/** Entries per page of the audit log's own keyset cursor. */
const AUDIT_PAGE = 30;
/** The jobs in hand shown above the timeline, at most. */
const RUNNING_SHOWN = 3;

export interface ActivityPageProps {
  search: ActivitySearch;
  onSearchChange: (search: ActivitySearch, options?: { replace?: boolean }) => void;
}

/** A change to the filters: a key set to undefined is cleared. */
type SearchPatch = { [K in keyof ActivitySearch]?: ActivitySearch[K] | undefined };

/**
 * What happened on this server and who did it (T1): every job Noust ran and every action the
 * audit log recorded, merged into one newest-first timeline. It opens on operations (what was
 * done to the server and its applications); sign-ins and access, and everything, are one
 * click away. The jobs running now lead the page; a job row opens its captured log. A session
 * that may not read the audit log sees jobs only, with a quiet note rather than an error.
 */
export function ActivityPage({ search, onSearchChange }: ActivityPageProps) {
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
  const active = useQuery(activeJobsQuery());

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
      }),
    [includeJobsQuery, allJobs, jobsComplete, includeAudit, allEntries, auditComplete, search.actor],
  );
  const rows = useMemo(
    () =>
      merged.rows.filter(
        (row) => inKind(row, search.kind) && matchesText(row, search.q ?? "", [actionWords(t, row).label, actorWords(t, row).label]),
      ),
    [merged.rows, search.kind, search.q, t],
  );

  const filtered = isFiltered(search);
  const onlyView = search.result === undefined && search.q === undefined && search.actor === undefined;
  const loading = (includeJobsQuery && jobs.isPending) || (includeAuditQuery && audit.isPending);
  const jobsHardError = includeJobsQuery && jobs.isError && jobs.data === undefined;
  const auditHardError = includeAuditQuery && !auditForbidden && audit.isError && audit.data === undefined;
  const loaded = (!includeJobsQuery || jobs.data !== undefined) && (!includeAuditQuery || auditForbidden || audit.data !== undefined);
  const nothingYet = loaded && !filtered && merged.rows.length === 0;
  const running = (active.data?.jobs ?? []).slice(0, RUNNING_SHOWN);

  const set = (patch: SearchPatch): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: ActivitySearch = {};
    if (next.kind) clean.kind = next.kind;
    if (next.result !== undefined && resultValidFor(next.result, clean.kind)) clean.result = next.result;
    if (next.q) clean.q = next.q;
    if (next.actor) clean.actor = next.actor;
    onSearchChange(clean, { replace: true });
  };

  const loadMore = (): void => {
    if (includeJobsQuery && !jobsComplete) setJobsLimit((limit) => limit + JOBS_PAGE);
    if (includeAudit && !auditComplete) void audit.fetchNextPage();
  };

  const notices = [
    ...running.map((job) => (
      <JobProgress
        key={job.id}
        state={job.status === "running" ? "running" : "queued"}
        title={job.name}
        step={job.current_step}
        announce={false}
        action={
          <Button size="sm" variant="ghost" icon={<ScrollText aria-hidden="true" />} onClick={() => setOpenJob(job)}>
            {t("activity.viewLog")}
          </Button>
        }
      />
    )),
    ...(auditForbidden ? [<Notice key="forbidden">{t("activity.auditForbidden")}</Notice>] : []),
  ];

  let content;
  if (jobsHardError || auditHardError) {
    const failing = jobsHardError ? jobs : audit;
    content = (
      <ErrorBlock
        error={failing.error}
        title={t("activity.couldNotLoad")}
        onRetry={() => {
          if (jobsHardError) void jobs.refetch();
          if (auditHardError) void audit.refetch();
        }}
        retrying={(jobsHardError && jobs.isRefetching) || (auditHardError && audit.isRefetching)}
      />
    );
  } else if (nothingYet) {
    content = (
      <EmptyState variant="firstUse" icon={<History />} title={t("activity.emptyTitle")} description={t("activity.emptyDescription")} command="noust audit list" />
    );
  } else {
    content = (
      <div className="flex flex-col gap-4">
        <ActivityTable
          rows={rows}
          caption={filtered ? t("activity.captionFiltered") : t("activity.caption")}
          loading={loading}
          onOpenJobLog={setOpenJob}
          empty={
            // Only the view chosen, and nothing in it: said as a fact, with nothing to clear.
            onlyView ? (
              <EmptyState variant="inline" title={search.kind === "access" ? t("activity.noAccessYet") : t("activity.noOperationsYet")} />
            ) : (
              <EmptyState
                variant="inline"
                title={merged.hasMore ? t("activity.noMatchYet") : t("activity.noMatch")}
                action={
                  <Button size="sm" variant="ghost" onClick={() => onSearchChange(search.kind !== undefined ? { kind: search.kind } : {})}>
                    {t("activity.clearFilters")}
                  </Button>
                }
              />
            )
          }
        />
        {merged.hasMore ? (
          <div>
            <Button
              size="sm"
              loading={(includeJobsQuery && !jobsComplete && jobs.isFetching) || (includeAudit && audit.isFetchingNextPage)}
              onClick={loadMore}
            >
              {t("activity.loadMore")}
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <ListPage
      header={{ title: t("activity.title"), description: t("activity.description") }}
      {...(notices.length > 0 ? { notice: <div className="flex flex-col gap-2">{notices}</div> } : {})}
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
                      value={search.kind ?? OPERATIONS}
                      onValueChange={(value) => set({ kind: value === "access" || value === "all" ? value : undefined })}
                      options={[
                        { value: OPERATIONS, label: t("activity.kindOperations") },
                        { value: "access", label: t("activity.kindAccess") },
                        { value: "all", label: t("activity.kindEverything") },
                      ]}
                    />
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
                        <Button size="sm" variant="ghost" onClick={() => onSearchChange(search.kind !== undefined ? { kind: search.kind } : {})}>
                          {t("activity.clearFilters")}
                        </Button>
                      ),
                    })}
              />
            ),
          })}
      {...(nothingYet ? {} : { footer: <CommandHint command="noust audit list" label={t("activity.fromTerminal")} /> })}
    >
      {content}
      <JobLogDrawer job={openJob} onOpenChange={(open) => !open && setOpenJob(null)} />
    </ListPage>
  );
}
