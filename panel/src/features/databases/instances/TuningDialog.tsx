import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { instanceKeys, tuningQuery } from "../../../api/queries/databaseInstances";
import type { DbInstance, TuningPlan, TuningStatus } from "../../../api/queries/databaseInstances";
import { jobKeys } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { draftOf, readTuning } from "./tuning";
import type { TuningDraft, TuningField } from "./tuning";
import type { TlsJobWords } from "./TlsDialog";
import { LoadingRegion } from "../../../components/page/LoadingRegion";

export interface TuningDialogProps {
  instance: DbInstance;
  onOpenChange: (open: boolean) => void;
  /** The job that does it, to follow, with the words that say what it does. */
  onQueued: (jobId: string, words: TlsJobWords) => void;
}

/** What Docker's probe says, as a state. */
function healthState(status: string): Status {
  return status === "healthy" ? "running" : status === "starting" ? "deploying" : status === "unhealthy" ? "failed" : "unknown";
}

/** A setting's name in words. */
function settingWord(t: T, setting: string): string {
  switch (setting) {
    case "memory":
      return t("databases.tuning.changes.memory");
    case "cpus":
      return t("databases.tuning.changes.cpus");
    case "swap":
      return t("databases.tuning.changes.swap");
    case "interval":
      return t("databases.tuning.changes.interval");
    case "timeout":
      return t("databases.tuning.changes.timeout");
    default:
      return t("databases.tuning.changes.retries");
  }
}

/** How Docker's probe is doing now, with the last probe's own output. */
function Health({ status }: { status: TuningStatus }) {
  const t = useT();
  const { health } = status;
  const word =
    health.status === "healthy"
      ? t("databases.tuning.health.healthy")
      : health.status === "starting"
        ? t("databases.tuning.health.starting")
        : health.status === "unhealthy"
          ? t("databases.tuning.health.unhealthy")
          : t("databases.tuning.health.absent");
  const output = health.last?.output ?? "";
  return (
    <div className="flex flex-col gap-2">
      <KeyValueList
        items={[
          {
            label: t("databases.tuning.healthNow"),
            value: <StatusPill size="sm" state={healthState(health.status)} label={word} />,
            copy: false,
            mono: false,
            ...(health.failing_streak > 0 ? { hint: t("databases.tuning.failingStreak", { count: health.failing_streak }) } : {}),
          },
        ]}
      />
      {output !== "" ? (
        <div className="flex flex-col gap-1">
          <p className="text-12 text-fg-muted">{t("databases.tuning.lastProbe")}</p>
          <SystemOutput label={t("databases.tuning.lastProbe")} maxHeight="max-h-24">
            {output}
          </SystemOutput>
        </div>
      ) : null}
    </div>
  );
}

/** What the change would do, as rows. Nothing here changes anything. */
function Plan({ plan }: { plan: UseQueryResult<TuningPlan> }) {
  const t = useT();
  if (plan.isPending) {
    return (
      <LoadingRegion label={t("databases.tuning.planning")}>
        <Skeleton className="h-24 w-full rounded-control" />
      </LoadingRegion>
    );
  }
  if (plan.isError) return <ErrorBlock compact error={plan.error} title={t("databases.tuning.planFailed")} />;
  const apps = plan.data.apps ?? [];
  const blockers = plan.data.blockers ?? [];
  const warnings = plan.data.warnings ?? [];
  const shown = (value: unknown): string => (typeof value === "number" || typeof value === "string" ? String(value) : t("databases.tuning.valueDefault"));
  return (
    <div className="flex flex-col gap-3">
      {blockers.length > 0 ? (
        <Notice tone="error" title={t("databases.tuning.blockedTitle")} live>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
      <KeyValueList
        items={[
          ...(plan.data.changes ?? []).map((change) => ({
            label: settingWord(t, String(change["setting"])),
            value: `${shown(change["from"])} → ${shown(change["to"])}`,
            copy: false as const,
          })),
          { label: t("databases.tuning.apps"), value: apps.length > 0 ? apps.join(", ") : t("databases.tuning.noApps"), mono: apps.length > 0, copy: false },
        ]}
      />
      {warnings.length > 0 ? (
        <Notice tone="warning" title={t("databases.tuning.warningsTitle")}>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}

/** The form, once the settings are known. */
function Form({ instance, status, onOpenChange, onQueued }: TuningDialogProps & { status: TuningStatus }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<TuningDraft>(() => draftOf(status));
  const reading = readTuning(status, draft);
  const hasInvalid = Object.keys(reading.invalid).length > 0;
  const set = (field: TuningField) => (value: string) => setDraft((previous) => ({ ...previous, [field]: value }));
  const error = (field: TuningField): string | undefined => (reading.invalid[field] === true ? t("databases.tuning.invalid") : undefined);

  const plan = useQuery({
    queryKey: ["databases", "instances", "tuning-plan", instance.name, reading.body] as const,
    queryFn: () => request("post", "/api/databases/instances/{name}/tuning/plan", { params: { name: instance.name }, body: reading.body }),
    enabled: reading.changed && !hasInvalid,
    retry: false,
    staleTime: 0,
  });
  const apply = useMutation({
    mutationFn: () => request("put", "/api/databases/instances/{name}/tuning", { params: { name: instance.name }, body: reading.body }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
      onQueued(accepted.job_id, {
        running: t("databases.tuning.applying", { name: instance.name }),
        succeeded: t("databases.tuning.applied", { name: instance.name }),
        failed: t("databases.tuning.applyFailed", { name: instance.name }),
      });
      onOpenChange(false);
    },
  });
  const blocked = !reading.changed || hasInvalid || plan.data === undefined || (plan.data.blockers ?? []).length > 0;
  const defaults = status.defaults;
  const server = status.server;
  const formId = "instance-tuning-form";

  return (
    <Dialog
      open
      onOpenChange={onOpenChange}
      size="lg"
      title={t("databases.tuning.title", { name: instance.name })}
      description={t("databases.tuning.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" disabled={blocked || apply.isPending} loading={apply.isPending}>
            {t("databases.tuning.apply")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <Health status={status} />
        <form
          id={formId}
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            if (!blocked) apply.mutate();
          }}
          className="flex flex-col gap-5"
        >
          <p className="text-13 font-medium text-fg">{t("databases.tuning.limitsTitle")}</p>
          <div className="flex flex-wrap gap-4">
            <Field label={t("databases.tuning.memory")} description={t("databases.tuning.memoryHelp", { available: server.available_mb })} error={error("memory")} className="w-56">
              <Input mono inputMode="numeric" value={draft.memory} onValueChange={set("memory")} suffix="MB" />
            </Field>
            <Field label={t("databases.tuning.cpus")} optional description={t("databases.tuning.cpusHelp", { count: server.cpus })} error={error("cpus")} className="w-44">
              <Input mono inputMode="decimal" value={draft.cpus} placeholder={t("databases.tuning.cpusPlaceholder")} onValueChange={set("cpus")} />
            </Field>
            <Field label={t("databases.tuning.swap")} optional description={t("databases.tuning.swapHelp")} error={error("swap")} className="w-56">
              <Input mono inputMode="numeric" value={draft.swap} placeholder={t("databases.tuning.swapPlaceholder")} onValueChange={set("swap")} suffix="MB" />
            </Field>
          </div>
          <p className="text-13 font-medium text-fg">{t("databases.tuning.probeTitle")}</p>
          <div className="flex flex-wrap gap-4">
            <Field label={t("databases.tuning.interval")} optional description={t("databases.tuning.intervalHelp", { value: defaults.interval_s })} error={error("interval")} className="w-44">
              <Input mono inputMode="numeric" value={draft.interval} onValueChange={set("interval")} suffix={t("databases.tuning.seconds")} />
            </Field>
            <Field label={t("databases.tuning.timeout")} optional description={t("databases.tuning.timeoutHelp", { value: defaults.timeout_s })} error={error("timeout")} className="w-44">
              <Input mono inputMode="numeric" value={draft.timeout} onValueChange={set("timeout")} suffix={t("databases.tuning.seconds")} />
            </Field>
            <Field label={t("databases.tuning.retries")} optional description={t("databases.tuning.retriesHelp", { value: defaults.retries })} error={error("retries")} className="w-44">
              <Input mono inputMode="numeric" value={draft.retries} onValueChange={set("retries")} suffix={t("databases.tuning.tries")} />
            </Field>
          </div>
        </form>
        {reading.changed && !hasInvalid ? (
          <div className="flex flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("databases.tuning.plan")}</p>
            <Plan plan={plan} />
          </div>
        ) : null}
        {apply.isError ? <ErrorBlock live compact error={apply.error} title={t("databases.tuning.failed")} /> : null}
        <CommandHint command={`noust db instance limits ${instance.name}`} label={t("common.words.fromTerminal")} />
      </div>
    </Dialog>
  );
}

/**
 * "Limits and health": what the instance may use (memory, CPUs, swap) and how Docker asks whether
 * it is well (interval, timeout, failures before it is called unhealthy), with what Docker says of
 * it now. A change restarts the engine behind the readiness gate (`PUT
 * /api/databases/instances/{name}/tuning`, sudo mode, a job); the plan is read first and a value the
 * server could not honour is refused before anything is queued.
 */
export function TuningDialog(props: TuningDialogProps) {
  const t = useT();
  const status = useQuery(tuningQuery(props.instance.name));
  if (status.data !== undefined) return <Form {...props} status={status.data} />;
  return (
    <Dialog
      open
      onOpenChange={props.onOpenChange}
      size="lg"
      title={t("databases.tuning.title", { name: props.instance.name })}
      description={t("databases.tuning.description")}
      footer={<Button onClick={() => props.onOpenChange(false)}>{t("common.confirmDialog.cancel")}</Button>}
    >
      {status.isError ? (
        <ErrorBlock compact error={status.error} title={t("databases.tuning.readFailed")} onRetry={() => void status.refetch()} />
      ) : (
        <LoadingRegion label={t("databases.tuning.loading")}>
          <Skeleton className="h-40 w-full rounded-control" />
        </LoadingRegion>
      )}
    </Dialog>
  );
}
