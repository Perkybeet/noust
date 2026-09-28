import { useQuery } from "@tanstack/react-query";
import { Clock, Plus, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { cronJobsQuery } from "../../api/queries/cron";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Input } from "../../components/ui/Input";
import { Kbd } from "../../components/ui/Kbd";
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

/** Every scheduled job on this machine: its schedule, next run and last result. */
export function CronPage({ search, onSearchChange }: CronPageProps) {
  const t = useT();
  const jobs = useQuery(cronJobsQuery());
  const [dialogJob, setDialogJob] = useState<CronJob | "new" | null>(null);
  const [runsFor, setRunsFor] = useState<string | null>(null);

  const all = useMemo(() => jobs.data?.jobs ?? [], [jobs.data]);
  const shown = useMemo(() => filterJobs(all, search), [all, search]);
  const filtered = isFiltered(search);
  const count =
    jobs.data === undefined
      ? null
      : filtered
        ? t("cron.page.jobsCountFiltered", { shown: shown.length, total: all.length })
        : t("cron.page.jobsCount", { count: all.length });

  const newJobButton = (
    <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setDialogJob("new")}>
      {t("cron.page.newJob")}
    </Button>
  );

  return (
    <>
      <PageHeader title={t("cron.page.title")} description={t("cron.page.description")} actions={newJobButton} />

      {jobs.isError && jobs.data === undefined ? (
        <ErrorBlock error={jobs.error} title={t("cron.page.loadError")} onRetry={() => void jobs.refetch()} retrying={jobs.isRefetching} />
      ) : jobs.data !== undefined && all.length === 0 ? (
        <EmptyState
          level={2}
          icon={<Clock />}
          title={t("cron.page.empty.title")}
          description={t("cron.page.empty.description")}
          action={newJobButton}
          command="wasm cron create nightly-report --schedule daily --command '...'"
          className="py-16"
        />
      ) : (
        <div className="flex flex-col gap-4">
          <div role="search" aria-label={t("cron.page.filterAria")} className="flex flex-wrap items-end gap-2">
            <Input
              type="search"
              aria-label={t("cron.page.searchAria")}
              placeholder={t("cron.page.searchPlaceholder")}
              data-page-search=""
              value={search.q ?? ""}
              onValueChange={(value: string) => onSearchChange(value === "" ? {} : { q: value }, { replace: true })}
              icon={<Search />}
              suffix={search.q ? undefined : <Kbd className="pointer-coarse:hidden">/</Kbd>}
              className="w-full sm:w-80"
              autoComplete="off"
              spellCheck={false}
            />
            {filtered ? (
              <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                {t("cron.common.clearFilters")}
              </Button>
            ) : null}
            <p role="status" className="ml-auto self-center text-13 text-fg-muted">
              {count ?? ""}
            </p>
          </div>

          <CronJobsTable
            jobs={shown}
            caption={filtered ? t("cron.table.captionFiltered") : t("cron.table.captionAll")}
            loading={jobs.isPending}
            rowActions={(job) => <CronJobRowActions job={job} onEdit={setDialogJob} onViewRuns={setRunsFor} />}
            empty={
              <EmptyState
                title={t("cron.page.noMatch.title")}
                description={t("cron.page.noMatch.description")}
                action={
                  <Button icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                    {t("cron.common.clearFilters")}
                  </Button>
                }
                className="border-0 py-8"
              />
            }
          />
          <CommandHint command="wasm cron list" label={t("cron.common.fromTerminal")} />
        </div>
      )}

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
    </>
  );
}
