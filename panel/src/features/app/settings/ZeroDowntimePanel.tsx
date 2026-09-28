import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, Radio, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { zeroDowntimeKey, zeroDowntimeQuery } from "../../../api/queries/zeroDowntime";
import type { ZeroDowntime, ZeroDowntimeInstance } from "../../../api/queries/zeroDowntime";
import { AppStatePill } from "../../../components/page/AppStatePill";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { splitErrors } from "../../settings/formErrors";
import { useConfirmItsYou } from "../useDeleteApp";
import { PANEL } from "./panel";
import { DRAIN_MAX, DRAIN_MIN, instanceName, parseDrain } from "./zeroDowntime";

/** What the job being followed does: turn the mode on, off, or only record a new drain. */
type Change = "on" | "off" | "drain";

interface ChangeRequest {
  change: Change;
  drain: number | null;
}

/** Both copies of the app, which one nginx sends the traffic to, and each unit's own state. */
function Instances({ status }: { status: ZeroDowntime }) {
  const t = useT();
  const instances = status.instances ?? [];
  if (instances.length === 0) return <p className="text-13 text-fg-muted">{t("appSettings.zeroDowntime.noInstances")}</p>;
  return (
    <ul aria-label={t("appSettings.zeroDowntime.instancesLabel")} className="flex flex-col divide-y divide-border rounded-control border border-border">
      {instances.map((instance: ZeroDowntimeInstance) => (
        <li key={instance.color} className="flex min-w-0 flex-col gap-1 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-13 font-medium text-fg">{instanceName(instance.color, t.locale)}</span>
            {instance.serving ? (
              <Badge>
                <Radio aria-hidden="true" className="size-3" />
                {t("appSettings.zeroDowntime.serving")}
              </Badge>
            ) : (
              <span className="text-12 text-fg-muted">{t("appSettings.zeroDowntime.idle")}</span>
            )}
            <AppStatePill status={instance.state} appearance="inline" size="sm" />
          </div>
          <p translate="no" className="mono text-12 break-all text-fg-muted">
            {t("appSettings.zeroDowntime.instanceLine", {
              port: instance.port,
              release: instance.release ?? t("appSettings.zeroDowntime.noRelease"),
              unit: instance.unit,
            })}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** Why the app cannot run as two instances, in the backend's words, with its fix. */
function NotEligible({ status }: { status: ZeroDowntime }) {
  const t = useT();
  return (
    <div className="flex items-start gap-2.5 rounded-control border border-border bg-bg-sunken px-3 py-2.5">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-faint" />
      <div className="flex min-w-0 flex-col gap-1 text-13">
        <p className="text-fg">{status.reason ?? t("appSettings.zeroDowntime.notEligibleDefault")}</p>
        {status.hint ? <p className="text-pretty text-fg-muted">{status.hint}</p> : null}
      </div>
    </div>
  );
}

/** What turning the mode on asks of the app, said before it is confirmed. */
function TwoCopiesWarning() {
  const t = useT();
  return (
    <div className="flex items-start gap-2.5 rounded-control border border-warn/40 bg-warn-soft px-3 py-2.5">
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
      <div className="flex min-w-0 flex-col gap-1 text-13 text-pretty">
        <p className="font-medium text-fg">{t("appSettings.zeroDowntime.twoCopiesTitle")}</p>
        <p className="text-fg">{t("appSettings.zeroDowntime.twoCopiesBody")}</p>
      </div>
    </div>
  );
}

/**
 * Blue/green activation for an app with a process of its own: when on, a new release starts
 * as a second instance on its own port and nginx's upstream moves to it once it passes the
 * health check; the old one stops after the drain. Whether the app can use it is the backend's
 * to say, and a refusal is shown in its words, with no control to try anyway. Switching it
 * rewrites units and the site, so it runs as a job, followed here like a migration.
 */
export function ZeroDowntimePanel({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const headingId = useId();
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const status = useQuery(zeroDowntimeQuery(domain));
  const followed = useFollowedJob();
  const notifiedRef = useRef<string | null>(null);
  const [confirming, setConfirming] = useState<"on" | "off" | null>(null);
  const [pending, setPending] = useState<ChangeRequest | null>(null);

  const current = status.data === undefined ? "" : String(status.data.drain_seconds);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [submitted, setSubmitted] = useState(false);
  // The mode changed underneath an untouched field (saved here, or from a terminal): follow it.
  if (current !== baseline) {
    setBaseline(current);
    if (draft.trim() === baseline) setDraft(current);
  }
  const parsed = parseDrain(draft, t.locale);

  const change = useMutation({
    mutationFn: ({ change: kind, drain }: ChangeRequest) =>
      request("put", "/api/apps/{domain}/zero-downtime", {
        params: { domain },
        body: { enabled: kind !== "off", ...(drain !== null ? { drain_seconds: drain } : {}) },
      }),
    onSuccess: (result) => {
      followed.follow(result.job as Job);
    },
  });

  const job = followed.job;
  const working = change.isPending || (job !== null && !isJobFinished(job));
  const jobFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  useEffect(() => {
    if (job === null || !isJobFinished(job) || job.status !== "completed" || notifiedRef.current === job.id) return;
    notifiedRef.current = job.id;
    setConfirming(null);
    setSubmitted(false);
    void queryClient.invalidateQueries({ queryKey: zeroDowntimeKey(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    // The toast is announced; saying it again would read it twice.
    toast.success(
      pending?.change === "drain"
        ? t("appSettings.zeroDowntime.savedDrain", { domain })
        : pending?.change === "off"
          ? t("appSettings.zeroDowntime.turnedOff", { domain })
          : t("appSettings.zeroDowntime.turnedOn", { domain }),
    );
  }, [job, domain, pending, queryClient, t]);

  // A refusal of the drain goes under the field; anything else is shown whole.
  const split = splitErrors(change.error, ["drain_seconds"] as const);
  const drainError = (submitted ? parsed.error : null) ?? split.fields.drain_seconds;
  const jobError = jobFailed ? { detail: job.error ?? t("appSettings.jobFailedSilently") } : null;
  const failure = change.isError && split.form !== null ? change.error : jobError;
  // In the dialog nothing is left out: the field it would go under is behind it.
  const dialogFailure = change.isError ? change.error : jobError;

  const start = (next: ChangeRequest, then: () => void): void => {
    change.reset();
    followed.dismiss();
    confirmItsYou().then(then, (error: unknown) => {
      if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.zeroDowntime.notChangedFor", { domain }), error);
    });
    setPending(next);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (parsed.seconds === null || working || status.data === undefined) return;
    const seconds = parsed.seconds;
    if (status.data.enabled) {
      if (draft.trim() === current) return;
      // Only records the drain: nothing starts or stops, so there is nothing to confirm.
      start({ change: "drain", drain: seconds }, () => {
        change.mutate({ change: "drain", drain: seconds });
      });
      return;
    }
    start({ change: "on", drain: seconds }, () => {
      setConfirming("on");
    });
  };

  const turnOff = (): void => {
    start({ change: "off", drain: null }, () => {
      setConfirming("off");
    });
  };

  const close = (next: boolean): void => {
    if (!next && working) return;
    if (!next) setConfirming(null);
  };

  const data = status.data;
  const failureTitle =
    pending?.change === "drain"
      ? t("appSettings.zeroDowntime.drainFailedTitle")
      : pending?.change === "off"
        ? t("appSettings.zeroDowntime.offFailedTitle")
        : t("appSettings.zeroDowntime.onFailedTitle");
  // A refusal is before anything ran; a job that failed was undone whole.
  const failureHint = jobFailed ? t("appSettings.zeroDowntime.revertedHint") : t("appSettings.zeroDowntime.nothingChangedHint");

  return (
    <section aria-labelledby={headingId} className={`${PANEL} flex flex-col gap-4 px-4 py-4 sm:px-5`}>
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={headingId} className="text-14 font-medium text-fg">
            {t("appSettings.zeroDowntime.title")}
          </h3>
          {data ? (
            data.enabled ? (
              <StatusPill state="running" label={t("appSettings.zeroDowntime.on")} size="sm" />
            ) : (
              <StatusPill state="stopped" label={t("appSettings.zeroDowntime.off")} size="sm" />
            )
          ) : null}
        </div>
        <p className="text-13 text-pretty text-fg-muted">{t("appSettings.zeroDowntime.description")}</p>
      </header>

      {status.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <span className="sr-only">{t("appSettings.zeroDowntime.loading")}</span>
          <Skeleton className="h-14 w-full rounded-control" />
        </div>
      ) : status.isError ? (
        <ErrorBlock
          compact
          error={status.error}
          title={t("appSettings.zeroDowntime.readFailed")}
          onRetry={() => void status.refetch()}
          retrying={status.isRefetching}
        />
      ) : data !== undefined && !data.enabled && !data.eligible ? (
        <NotEligible status={data} />
      ) : data !== undefined ? (
        <form onSubmit={submit} noValidate className="flex flex-col gap-4">
          {data.enabled ? (
            <>
              <Instances status={data} />
              {data.upstream_port !== null && data.upstream_port !== undefined ? (
                <p className="text-12 text-fg-muted">
                  {t.rich("appSettings.zeroDowntime.upstreamNames", {
                    port: (
                      <span translate="no" className="mono text-fg">
                        {String(data.upstream_port)}
                      </span>
                    ),
                  })}
                </p>
              ) : null}
            </>
          ) : null}
          <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
            <Field
              label={t("appSettings.zeroDowntime.drainLabel")}
              description={t("appSettings.zeroDowntime.drainDescription", { min: DRAIN_MIN, max: DRAIN_MAX })}
              error={drainError}
              className="w-44"
            >
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                suffix="s"
                value={draft}
                onValueChange={(value: string) => {
                  setDraft(value);
                  if (change.isError) change.reset();
                }}
              />
            </Field>
            {data.enabled ? (
              <Button type="submit" variant="primary" disabled={draft.trim() === current} loading={working && pending?.change === "drain"} className="sm:mt-6">
                {t("appSettings.save")}
              </Button>
            ) : null}
          </div>
          {confirming === null && failure !== null ? <ErrorBlock live compact error={failure} title={failureTitle} hint={failureHint} /> : null}
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            {data.enabled ? (
              <Button variant="ghost" disabled={working} onClick={turnOff}>
                {t("appSettings.zeroDowntime.turnOff")}
              </Button>
            ) : (
              <Button type="submit" disabled={working}>
                {t("appSettings.zeroDowntime.turnOn")}
              </Button>
            )}
          </div>
        </form>
      ) : null}

      <CommandHint command={`wasm app zero-downtime ${domain} on --drain 10`} label={t("appSettings.fromTerminal")} />

      <Dialog
        open={confirming !== null}
        onOpenChange={close}
        title={
          confirming === "off"
            ? t("appSettings.zeroDowntime.confirmTurnOffTitle", { domain })
            : t("appSettings.zeroDowntime.confirmTurnOnTitle", { domain })
        }
        description={
          confirming === "off"
            ? t("appSettings.zeroDowntime.confirmTurnOffDescription")
            : t("appSettings.zeroDowntime.confirmTurnOnDescription", { seconds: pending?.drain ?? data?.drain_seconds ?? 0 })
        }
        footer={
          <>
            <Button disabled={working} onClick={() => close(false)}>
              {t("appSettings.cancel")}
            </Button>
            {confirming === "off" ? (
              <Button variant="danger" loading={working} onClick={() => change.mutate({ change: "off", drain: null })}>
                {t("appSettings.zeroDowntime.turnOff")}
              </Button>
            ) : (
              <Button variant="primary" loading={working} onClick={() => change.mutate({ change: "on", drain: pending?.drain ?? null })}>
                {t("appSettings.zeroDowntime.turnOn")}
              </Button>
            )}
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {confirming === "on" ? <TwoCopiesWarning /> : null}
          {working ? (
            <p role="status" className="text-13 text-fg-muted">
              {confirming === "off" ? t("appSettings.zeroDowntime.turningOff") : t("appSettings.zeroDowntime.turningOn")}
            </p>
          ) : dialogFailure !== null ? (
            <ErrorBlock live compact error={dialogFailure} title={failureTitle} hint={failureHint} />
          ) : null}
        </div>
      </Dialog>
    </section>
  );
}
