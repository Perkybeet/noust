import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { ServerText } from "../../../components/ui/ServerText";
import type { CodedText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { NODE_JOB_VIEW, fleetJobQuery, isJobRunning, nodeJobState } from "../../fleet/data";
import type { FleetNodeState } from "../../fleet/data";
import { applyToFleet, fleetSettingsKeys } from "./data";

/** One thing the job did on a server, as the central recorded it. */
interface Item {
  resource: string;
  name: string;
  state: string;
  message?: string | null;
  coded?: CodedText | null;
  output?: string | null;
}

const RESULT_STATES = ["created", "updated", "restored", "in_sync", "overridden", "released", "conflict", "unsupported"] as const;
type ResultState = (typeof RESULT_STATES)[number];

function resultWord(t: T, state: string): string {
  if (state === "failed") return t("common.statusPill.failed");
  return (RESULT_STATES as readonly string[]).includes(state) ? t(`fleetSettings.applyDialog.result.${state as ResultState}`) : state;
}

function itemsOf(node: FleetNodeState): Item[] {
  return node.items as unknown as Item[];
}

/** What a server did: its state, then the settings that did not go as asked, in the server's own words. */
function ServerProgress({ t, node }: { t: T; node: FleetNodeState }) {
  const view = NODE_JOB_VIEW[nodeJobState(node.state)];
  const items = itemsOf(node);
  const problems = items.filter((item) => item.state === "failed" || item.state === "conflict" || item.state === "unsupported");
  const changed = items.filter((item) => ["created", "updated", "restored", "released"].includes(item.state)).length;
  return (
    <RowList.Item className="flex-col items-stretch gap-1.5">
      <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <Mono tone="default">{node.node}</Mono>
        <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
        {items.length > 0 ? <span className="text-12 text-fg-muted">{changed > 0 ? `${t("fleetSettings.applyDialog.result.updated")}: ${String(changed)}` : t("fleetSettings.applyDialog.result.in_sync")}</span> : null}
      </span>
      {problems.map((item) => (
        <span key={`${item.resource}/${item.name}`} className="flex min-w-0 flex-col gap-0.5 text-13">
          <span>
            <Mono tone="default">{item.name}</Mono> <span className="text-fg-muted">{resultWord(t, item.state)}</span>
          </span>
          {item.message !== undefined && item.message !== null ? (
            <span className="text-fg-muted">
              <ServerText coded={item.coded ?? null} text={item.message} />
            </span>
          ) : null}
          {item.output !== undefined && item.output !== null && item.output !== "" ? <SystemOutput label={t("fleetSettings.applyDialog.outputLabel", { name: item.name })}>{item.output}</SystemOutput> : null}
        </span>
      ))}
    </RowList.Item>
  );
}

/** The servers of a running job, each in its own state, read again every two seconds until it is over. */
function JobProgress({ t, id }: { t: T; id: string }) {
  const job = useQuery(fleetJobQuery(id));
  if (job.data === undefined) return <Skeleton className="h-24 w-full rounded-card" />;
  return (
    <div className="flex flex-col gap-3">
      <RowList aria-label={t("fleetSettings.applyDialog.progress")}>
        {(job.data.nodes ?? []).map((node) => (
          <ServerProgress key={node.node} t={t} node={node} />
        ))}
      </RowList>
      <p className="text-13">
        <TextLink to="/fleet/jobs/$id" params={{ id }} size="ui">
          {t("fleetSettings.applyDialog.open")}
        </TextLink>
        {isJobRunning(job.data) ? null : <span className="ml-3 text-fg-muted">{t("common.words.done")}</span>}
      </p>
    </div>
  );
}

/**
 * "Apply to the fleet": what would run, then the job that does it, with each server in its own state (item 90).
 * Every server is asked what it holds and is given what the fleet says; what a server already holds that is not
 * the fleet's, and what its operator changed, are left unless the person says to take them over or put them back.
 */
export function ApplyDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [adopt, setAdopt] = useState(false);
  const [restoreAll, setRestoreAll] = useState(false);
  const plan = useQuery({
    queryKey: ["fleet", "settings-plan", adopt, restoreAll],
    queryFn: () => applyToFleet({ plan: true, adopt, restoreAll }),
  });
  const start = useMutation({
    mutationFn: () => applyToFleet({ plan: false, adopt, restoreAll }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
    },
  });
  const jobId = start.data?.job?.id ?? null;

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !start.isPending) onClose();
      }}
      size="md"
      title={t("fleetSettings.applyDialog.title")}
      description={t("fleetSettings.applyDialog.description")}
      footer={
        jobId !== null ? (
          <Button variant="primary" onClick={onClose}>
            {t("common.words.close")}
          </Button>
        ) : (
          <>
            <Button disabled={start.isPending} onClick={onClose}>
              {t("common.words.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={start.isPending || plan.isPending}>
              {t("fleetSettings.applyDialog.start")}
            </Button>
          </>
        )
      }
    >
      {jobId !== null ? (
        <JobProgress t={t} id={jobId} />
      ) : (
        <form
          id={formId}
          noValidate
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            // With nothing to run the dialog already says so, above: pressing Apply then does nothing.
            if (!start.isPending && plan.data !== undefined && plan.data.plan.summary.run > 0) start.mutate();
          }}
        >
          {plan.isPending ? <Skeleton className="h-16 w-full rounded-card" /> : null}
          {plan.isError ? <ErrorBlock compact error={plan.error} title={t("fleetSettings.applyDialog.planFailed")} onRetry={() => void plan.refetch()} /> : null}
          {plan.data !== undefined ? (
            <div className="flex flex-col gap-1">
              {plan.data.plan.summary.run === 0 ? (
                <p className="text-14 text-fg">{t("fleetSettings.applyDialog.nothing")}</p>
              ) : (
                <p className="text-14 text-fg">{t("fleetSettings.applyDialog.summary", { count: plan.data.plan.summary.run })}</p>
              )}
              {plan.data.plan.summary.skipped > 0 ? <p className="text-13 text-fg-muted">{t("fleetSettings.applyDialog.skipped", { count: plan.data.plan.summary.skipped })}</p> : null}
            </div>
          ) : null}
          <div className="flex flex-col gap-3">
            <Checkbox label={t("fleetSettings.applyDialog.adopt")} description={t("fleetSettings.applyDialog.adoptHelp")} checked={adopt} onCheckedChange={setAdopt} />
            <Checkbox label={t("fleetSettings.applyDialog.restore")} checked={restoreAll} onCheckedChange={setRestoreAll} />
          </div>
          {start.isError ? <ErrorBlock live compact error={start.error} title={t("fleetSettings.applyDialog.failed")} /> : null}
        </form>
      )}
    </Dialog>
  );
}
