import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import type { Job } from "../../../api/queries/jobs";
import { zeroDowntimeKey } from "../../../api/queries/zeroDowntime";
import type { ZeroDowntime, ZeroDowntimeInstance } from "../../../api/queries/zeroDowntime";
import { announce } from "../../../app/Announcer";
import { AppStatePill } from "../../../components/page/AppStatePill";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { useSudoFirst } from "./formParts";
import type { FormPart } from "./formParts";
import { JobFailedError, waitForJob } from "./queries";
import { DRAIN_MAX, DRAIN_MIN, instanceName, parseDrain } from "./zeroDowntime";

/** Compose's app type: zero-downtime deploys relay its web services instead of running two units. */
const COMPOSE = "docker-compose";

/**
 * What stands in the way, each with its fix, as the backend lists them ("- one per line"); a
 * hint that is not a list is its own sentence. Shown as written: they name files and lines.
 */
function Problems({ hint, label }: { hint: string | null; label: string }) {
  if (hint === null || hint.trim() === "") return null;
  const lines = hint.split("\n").filter((line) => line.trim() !== "");
  const listed = lines.every((line) => line.startsWith("- "));
  if (!listed) return <p className="break-words">{hint}</p>;
  return (
    <ul aria-label={label} className="flex list-disc flex-col gap-1 pl-5">
      {lines.map((line) => (
        <li key={line} className="break-words">
          {line.slice(2)}
        </li>
      ))}
    </ul>
  );
}

/** Asks for the mode and waits for the job it queues: done means done, not queued. */
async function setMode(queryClient: ReturnType<typeof useQueryClient>, domain: string, body: { enabled: boolean; drain_seconds?: number }): Promise<void> {
  const queued = await request("put", "/api/apps/{domain}/zero-downtime", { params: { domain }, body });
  const job = await waitForJob(queryClient, queued.job as Job);
  void queryClient.invalidateQueries({ queryKey: zeroDowntimeKey(domain) });
  void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
  void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
  if (job.status !== "completed") throw new JobFailedError(job.error ?? "");
}

export interface DrainPart {
  /** Null while the mode is off: the drain is then asked for when turning it on. */
  part: FormPart | null;
  draft: string;
  edit: (value: string) => void;
  error: string | undefined;
  failure: unknown;
}

/**
 * How long the old version keeps running after traffic moves, as a part of the Deploys form
 * while zero-downtime deploys are on. Saving it only records the number: nothing starts or
 * stops, but it goes through the same job as the mode itself, which the save waits for.
 */
export function useDrain(domain: string, status: ZeroDowntime | undefined): DrainPart {
  const t = useT();
  const queryClient = useQueryClient();
  const current = status === undefined ? "" : String(status.drain_seconds);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [submitted, setSubmitted] = useState(false);
  // The mode changed underneath an untouched field (saved here, or from a terminal): follow it.
  if (current !== baseline) {
    setBaseline(current);
    if (draft.trim() === baseline) setDraft(current);
  }
  const parsed = parseDrain(draft, t.locale);
  const save = useMutation({
    mutationFn: (seconds: number) => setMode(queryClient, domain, { enabled: true, drain_seconds: seconds }),
    onSuccess: (_result, seconds) => {
      // What was saved is what the field now says, whatever the next read says.
      setDraft(String(seconds));
      setBaseline(String(seconds));
      setSubmitted(false);
    },
  });
  const split = splitErrors(save.error, ["drain_seconds"] as const);
  const error = (submitted && parsed.error !== null ? parsed.error : undefined) ?? split.fields.drain_seconds;

  return {
    part:
      status?.enabled === true
        ? {
            changes: draft.trim() !== current ? 1 : 0,
            elevated: true,
            check: () => {
              setSubmitted(true);
              return parsed.seconds !== null;
            },
            save: async () => {
              if (parsed.seconds === null) return;
              await save.mutateAsync(parsed.seconds);
            },
            discard: () => {
              setDraft(current);
              setSubmitted(false);
              save.reset();
            },
          }
        : null,
    draft,
    edit: (value) => {
      setDraft(value);
      if (save.isError) save.reset();
    },
    error,
    failure: save.isError && split.form !== null ? save.error : null,
  };
}

/** Both copies of the app, which one takes the traffic, and each one's own state. */
function Instances({ status }: { status: ZeroDowntime }) {
  const t = useT();
  const instances = status.instances ?? [];
  if (instances.length === 0) return <p className="text-13 text-fg-muted">{t("appSettings.zeroDowntime.noInstances")}</p>;
  return (
    <ul aria-label={t("appSettings.zeroDowntime.instancesLabel")} className="flex flex-col divide-y divide-border rounded-control border border-border">
      {instances.map((instance: ZeroDowntimeInstance) => (
        <li key={instance.color} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
          <span className="text-13 font-medium text-fg">{instanceName(instance.color, t.locale)}</span>
          {instance.serving ? (
            <Badge>
              <Radio aria-hidden="true" className="size-icon-xs" />
              {t("appSettings.zeroDowntime.serving")}
            </Badge>
          ) : (
            <span className="text-12 text-fg-muted">{t("appSettings.zeroDowntime.idle")}</span>
          )}
          <AppStatePill status={instance.state} appearance="inline" size="sm" />
          <span className="text-12 text-fg-muted">
            {t.rich("appSettings.zeroDowntime.instanceLine", {
              port: <Mono>{instance.port}</Mono>,
              release: <Mono>{instance.release ?? t("appSettings.zeroDowntime.noRelease")}</Mono>,
            })}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Turning it on: what two copies at once asks of the app, and how long the old one stays up. */
function TurnOnDialog({
  domain,
  drainSeconds,
  compose,
  open,
  onOpenChange,
}: {
  domain: string;
  drainSeconds: number;
  compose: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(String(drainSeconds));
  const [submitted, setSubmitted] = useState(false);
  const parsed = parseDrain(draft, t.locale);
  const turnOn = useMutation({
    mutationFn: (seconds: number) => setMode(queryClient, domain, { enabled: true, drain_seconds: seconds }),
    onSuccess: () => {
      onOpenChange(false);
      setSubmitted(false);
      announce(t("appSettings.zeroDowntime.turnedOn", { domain }));
    },
  });
  const split = splitErrors(turnOn.error, ["drain_seconds"] as const);
  const error = (submitted && parsed.error !== null ? parsed.error : undefined) ?? split.fields.drain_seconds;
  const failure = turnOn.isError && split.form !== null ? turnOn.error : null;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (parsed.seconds === null || turnOn.isPending) return;
    turnOn.mutate(parsed.seconds);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && turnOn.isPending) return;
        if (!next) turnOn.reset();
        onOpenChange(next);
      }}
      title={t("appSettings.zeroDowntime.confirmTurnOnTitle", { domain })}
      description={compose ? t("appSettings.zeroDowntime.composeConfirmTurnOnDescription") : t("appSettings.zeroDowntime.confirmTurnOnDescription")}
      footer={
        <>
          <Button disabled={turnOn.isPending} onClick={() => onOpenChange(false)}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form="zero-downtime-on" variant="primary" loading={turnOn.isPending}>
            {t("appSettings.zeroDowntime.turnOn")}
          </Button>
        </>
      }
    >
      <form id="zero-downtime-on" onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Notice tone="warning" title={compose ? t("appSettings.zeroDowntime.composeTwoCopiesTitle") : t("appSettings.zeroDowntime.twoCopiesTitle")}>
          {compose ? t("appSettings.zeroDowntime.composeTwoCopiesBody") : t("appSettings.zeroDowntime.twoCopiesBody")}
        </Notice>
        <Field label={t("appSettings.zeroDowntime.drainLabel")} description={t("appSettings.zeroDowntime.drainDescription", { min: DRAIN_MIN, max: DRAIN_MAX })} error={error}>
          <Input mono inputMode="numeric" autoComplete="off" suffix="s" value={draft} onValueChange={(value: string) => setDraft(value)} className="w-32" />
        </Field>
        {turnOn.isPending ? (
          <p role="status" className="text-13 text-fg-muted">
            {t("appSettings.zeroDowntime.turningOn")}
          </p>
        ) : failure !== null ? (
          <ErrorBlock
            live
            compact
            error={failure}
            title={t("appSettings.zeroDowntime.onFailedTitle")}
            hint={failure instanceof JobFailedError ? t("appSettings.zeroDowntime.revertedHint") : t("appSettings.zeroDowntime.nothingChangedHint")}
          />
        ) : null}
      </form>
    </Dialog>
  );
}

/**
 * Zero-downtime deploys, for an app with a process of its own: the new version starts beside
 * the old one and traffic moves once it answers. Whether the app can use it is the backend's to
 * say, in its words; an app still in a single folder is told it needs instant rollback first.
 * Turning it on or off rewrites units and the site, so each runs as a job, waited for here.
 */
export function ZeroDowntimeCard({ app, status, drain }: { app: App; status: UseQueryResult<ZeroDowntime>; drain: DrainPart }) {
  const t = useT();
  const domain = app.domain;
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const [turningOn, setTurningOn] = useState(false);
  const [turningOff, setTurningOff] = useState(false);
  const data = status.data;

  const ask = (then: () => void): void => {
    sudoFirst(then, t("appSettings.zeroDowntime.notChangedFor", { domain }));
  };

  const title = t("appSettings.zeroDowntime.title");
  if (status.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("appSettings.zeroDowntime.loading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  if (status.isError || data === undefined) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={status.error} title={t("appSettings.zeroDowntime.readFailed")} onRetry={() => void status.refetch()} retrying={status.isRefetching} />
      </Card>
    );
  }
  const compose = app.app_type === COMPOSE;
  const description = compose ? t("appSettings.zeroDowntime.composeDescription") : t("appSettings.zeroDowntime.description");
  if (!data.enabled && !data.eligible) {
    return (
      <Card title={title} description={description}>
        {app.layout !== "releases" && !compose ? (
          <FeatureState state="off" title={t("appSettings.zeroDowntime.off")}>
            {t("appSettings.zeroDowntime.needsRollback")}
          </FeatureState>
        ) : (
          <FeatureState state="off" title={t("appSettings.zeroDowntime.offCannot")}>
            <div className="flex flex-col gap-2">
              <p className="break-words">{data.reason ?? t("appSettings.zeroDowntime.notEligibleDefault")}</p>
              <Problems hint={data.hint ?? null} label={t("appSettings.zeroDowntime.problemsLabel")} />
            </div>
          </FeatureState>
        )}
      </Card>
    );
  }

  return (
    <Card title={title} description={description}>
      {data.enabled ? (
        <div className="flex flex-col gap-4">
          {data.reason ? (
            <FeatureState state="problem" title={<span className="break-words">{data.reason}</span>}>
              <Problems hint={data.hint ?? null} label={t("appSettings.zeroDowntime.problemsLabel")} />
            </FeatureState>
          ) : (
            <FeatureState state="on" title={t("appSettings.zeroDowntime.on")}>
              {compose ? t("appSettings.zeroDowntime.composeOnBody") : t("appSettings.zeroDowntime.onBody")}
            </FeatureState>
          )}
          {compose && (data.instances ?? []).length === 0 ? null : <Instances status={data} />}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <Field
              label={t("appSettings.zeroDowntime.drainLabel")}
              description={t("appSettings.zeroDowntime.drainDescription", { min: DRAIN_MIN, max: DRAIN_MAX })}
              error={drain.error}
              className="max-w-measure-help"
            >
              <Input mono inputMode="numeric" autoComplete="off" suffix="s" value={drain.draft} onValueChange={drain.edit} className="w-32" />
            </Field>
            <Button size="sm" variant="ghost" onClick={() => ask(() => setTurningOff(true))} className="self-start sm:self-auto">
              {t("appSettings.zeroDowntime.turnOff")}
            </Button>
          </div>
          {drain.failure !== null ? (
            <ErrorBlock
              live
              compact
              error={drain.failure}
              title={t("appSettings.zeroDowntime.drainFailedTitle")}
              hint={drain.failure instanceof JobFailedError ? t("appSettings.zeroDowntime.revertedHint") : t("appSettings.zeroDowntime.nothingChangedHint")}
            />
          ) : null}
        </div>
      ) : (
        <FeatureState
          state="off"
          title={t("appSettings.zeroDowntime.off")}
          action={
            <Button size="sm" onClick={() => ask(() => setTurningOn(true))}>
              {t("appSettings.zeroDowntime.turnOnEllipsis")}
            </Button>
          }
        >
          {t("appSettings.zeroDowntime.offNote")}
        </FeatureState>
      )}
      <TurnOnDialog key={String(turningOn)} domain={domain} drainSeconds={data.drain_seconds} compose={compose} open={turningOn} onOpenChange={setTurningOn} />
      <ConfirmDialog
        open={turningOff}
        onOpenChange={setTurningOff}
        friction="simple"
        server={node}
        title={t("appSettings.zeroDowntime.confirmTurnOffTitle", { domain })}
        description={t("appSettings.zeroDowntime.confirmTurnOffDescription")}
        actionLabel={t("appSettings.zeroDowntime.turnOff")}
        onConfirm={async () => {
          await setMode(queryClient, domain, { enabled: false });
          announce(t("appSettings.zeroDowntime.turnedOff", { domain }));
        }}
      />
    </Card>
  );
}
