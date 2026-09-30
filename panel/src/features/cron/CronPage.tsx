import { useQuery } from "@tanstack/react-query";
import { Clock } from "lucide-react";
import { useMemo, useState } from "react";

import { cronJobsQuery } from "../../api/queries/cron";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { useT } from "../../i18n";
import { CronJobDialog } from "./CronJobDialog";
import { CronJobRowActions } from "./CronJobRowActions";
import { CronJobsTable } from "./CronJobsTable";
import { CronRunsDrawer } from "./CronRunsDrawer";
import { filterJobs, isFiltered } from "./data";
import type { CronJob, CronSearch } from "./data";

export interface CronPageProps {
  search: CronSearch;
  onSearchChange: (search: CronSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every scheduled job on this machine (T1): how its last run ended, when it runs, and when it
 * runs next. A row opens its run history in a drawer; its menu runs it now, edits, pauses or
 * deletes it.
 */
export function CronPage({ search, onSearchChange }: CronPageProps) {
  const t = useT();
  const jobs = useQuery(cronJobsQuery());
  const [dialogJob, setDialogJob] = useState<CronJob | "new" | null>(null);
  const [runsFor, setRunsFor] = useState<string | null>(null);

  const all = useMemo(() => jobs.data?.jobs ?? [], [jobs.data]);
  const shown = useMemo(() => filterJobs(all, search), [all, search]);
  const filtered = isFiltered(search);
  const empty = jobs.data !== undefined && all.length === 0;

  const newJobButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<ICONS.add aria-hidden="true" />} onClick={() => setDialogJob("new")}>
      {t("cron.page.newJob")}
    </Button>
  );

  let content;
  if (jobs.isError && jobs.data === undefined) {
    content = <ErrorBlock error={jobs.error} title={t("cron.page.loadError")} onRetry={() => void jobs.refetch()} retrying={jobs.isRefetching} />;
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<Clock />}
        title={t("cron.page.empty.title")}
        description={t("cron.page.empty.description")}
        action={newJobButton("secondary")}
        command="noust cron create nightly-report --schedule daily --command '...'"
      />
    );
  } else {
    content = (
      <CronJobsTable
        jobs={shown}
        caption={filtered ? t("cron.table.captionFiltered") : t("cron.table.captionAll")}
        loading={jobs.isPending}
        onRowActivate={(job) => setRunsFor(job.name)}
        rowActions={(job) => <CronJobRowActions job={job} onEdit={setDialogJob} onViewRuns={setRunsFor} />}
        empty={
          <EmptyState
            variant="inline"
            title={t("cron.page.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
                {t("cron.common.clearFilters")}
              </Button>
            }
          />
        }
      />
    );
  }

  return (
    <ListPage
      header={{ title: t("cron.page.title"), description: t("cron.page.description"), primaryAction: newJobButton("primary") }}
      {...(empty
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("cron.page.filterAria")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => onSearchChange(value === "" ? {} : { q: value }, { replace: true }),
                  label: t("cron.page.searchAria"),
                  placeholder: t("cron.page.searchPlaceholder"),
                }}
                {...(jobs.data !== undefined
                  ? {
                      count: filtered
                        ? t("cron.page.jobsCountFiltered", { shown: shown.length, total: all.length })
                        : t("cron.page.jobsCount", { count: all.length }),
                    }
                  : {})}
              />
            ),
          })}
      {...(empty ? {} : { footer: <CommandHint command="noust cron list" label={t("cron.common.fromTerminal")} /> })}
    >
      {content}

      <CronJobDialog
        // Remounts per job so a form field's local state never leaks from editing one job into
        // creating or editing another.
        key={dialogJob === null ? "closed" : dialogJob === "new" ? "new" : dialogJob.name}
        open={dialogJob !== null}
        onOpenChange={(open) => {
          if (!open) setDialogJob(null);
        }}
        {...(dialogJob !== null && dialogJob !== "new" ? { job: dialogJob } : {})}
      />
      <CronRunsDrawer name={runsFor} onOpenChange={(open) => !open && setRunsFor(null)} />
    </ListPage>
  );
}
