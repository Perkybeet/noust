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
  const instances = status.instances ?? [];
  if (instances.length === 0) return <p className="text-13 text-fg-muted">No instance is recorded yet.</p>;
  return (
    <ul aria-label="Instances" className="flex flex-col divide-y divide-border rounded-control border border-border">
      {instances.map((instance: ZeroDowntimeInstance) => (
        <li key={instance.color} className="flex min-w-0 flex-col gap-1 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-13 font-medium text-fg">{instanceName(instance.color)}</span>
            {instance.serving ? (
              <Badge>
                <Radio aria-hidden="true" className="size-3" />
                Serving
              </Badge>
            ) : (
              <span className="text-12 text-fg-muted">Idle</span>
            )}
            <AppStatePill status={instance.state} appearance="inline" size="sm" />
          </div>
          <p translate="no" className="mono text-12 break-all text-fg-muted">
            {`Port ${String(instance.port)} · Release ${instance.release ?? "none"} · ${instance.unit}.service`}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** Why the app cannot run as two instances, in the backend's words, with its fix. */
function NotEligible({ status }: { status: ZeroDowntime }) {
  return (
    <div className="flex items-start gap-2.5 rounded-control border border-border bg-bg-sunken px-3 py-2.5">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-faint" />
      <div className="flex min-w-0 flex-col gap-1 text-13">
        <p className="text-fg">{status.reason ?? "This app cannot run as two instances."}</p>
        {status.hint ? <p className="text-pretty text-fg-muted">{status.hint}</p> : null}
      </div>
    </div>
  );
}

/** What turning the mode on asks of the app, said before it is confirmed. */
function TwoCopiesWarning() {
  return (
    <div className="flex items-start gap-2.5 rounded-control border border-warn/40 bg-warn-soft px-3 py-2.5">
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
      <div className="flex min-w-0 flex-col gap-1 text-13 text-pretty">
        <p className="font-medium text-fg">Two copies of the app run at once for a few seconds.</p>
        <p className="text-fg">
          Only turn this on if the app tolerates that. These do not: a SQLite database both copies write to, a queue that must
          have a single consumer, and a scheduler inside the process, whose jobs would run twice.
        </p>
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
  const parsed = parseDrain(draft);

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
        ? `Saved the drain of ${domain}`
        : pending?.change === "off"
          ? `Zero downtime is off for ${domain}`
          : `Zero downtime is on for ${domain}`,
    );
  }, [job, domain, pending, queryClient]);

  // A refusal of the drain goes under the field; anything else is shown whole.
  const split = splitErrors(change.error, ["drain_seconds"] as const);
  const drainError = (submitted ? parsed.error : null) ?? split.fields.drain_seconds;
  const jobError = jobFailed ? { detail: job.error ?? "The job failed without saying why. Its log is on the Activity page." } : null;
  const failure = change.isError && split.form !== null ? change.error : jobError;
  // In the dialog nothing is left out: the field it would go under is behind it.
  const dialogFailure = change.isError ? change.error : jobError;

  const start = (next: ChangeRequest, then: () => void): void => {
    change.reset();
    followed.dismiss();
    confirmItsYou().then(then, (error: unknown) => {
      if (!(error instanceof ElevationCancelledError)) reportActionError(`Zero downtime of ${domain} was not changed`, error);
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
  const failureTitle = pending?.change === "drain" ? "The drain was not saved" : pending?.change === "off" ? "Zero downtime was not turned off" : "Zero downtime was not turned on";
  // A refusal is before anything ran; a job that failed was undone whole.
  const failureHint = jobFailed ? "WASM put back what served before: the app serves as it did." : "Nothing was changed.";

  return (
    <section aria-labelledby={headingId} className={`${PANEL} flex flex-col gap-4 px-4 py-4 sm:px-5`}>
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={headingId} className="text-14 font-medium text-fg">
            Zero downtime
          </h3>
          {data ? data.enabled ? <StatusPill state="running" label="On" size="sm" /> : <StatusPill state="stopped" label="Off" size="sm" /> : null}
        </div>
        <p className="text-13 text-pretty text-fg-muted">
          Blue/green activation: a new release starts as a second instance on its own port, and traffic moves to it once it passes
          the health check. Off, an activation restarts the one unit, which drops connections for a moment.
        </p>
      </header>

      {status.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <span className="sr-only">Loading zero downtime</span>
          <Skeleton className="h-14 w-full rounded-control" />
        </div>
      ) : status.isError ? (
        <ErrorBlock compact error={status.error} title="Could not read zero downtime" onRetry={() => void status.refetch()} retrying={status.isRefetching} />
      ) : data !== undefined && !data.enabled && !data.eligible ? (
        <NotEligible status={data} />
      ) : data !== undefined ? (
        <form onSubmit={submit} noValidate className="flex flex-col gap-4">
          {data.enabled ? (
            <>
              <Instances status={data} />
              {data.upstream_port !== null && data.upstream_port !== undefined ? (
                <p className="text-12 text-fg-muted">
                  {"nginx's upstream names port "}
                  <span translate="no" className="mono text-fg">
                    {String(data.upstream_port)}
                  </span>
                  .
                </p>
              ) : null}
            </>
          ) : null}
          <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
            <Field
              label="Drain"
              description={`Seconds the old instance keeps running after traffic moves, ${String(DRAIN_MIN)} to ${String(DRAIN_MAX)}.`}
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
                Save
              </Button>
            ) : null}
          </div>
          {confirming === null && failure !== null ? <ErrorBlock live compact error={failure} title={failureTitle} hint={failureHint} /> : null}
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            {data.enabled ? (
              <Button variant="ghost" disabled={working} onClick={turnOff}>
                Turn off zero downtime
              </Button>
            ) : (
              <Button type="submit" disabled={working}>
                Turn on zero downtime
              </Button>
            )}
          </div>
        </form>
      ) : null}

      <CommandHint command={`wasm app zero-downtime ${domain} on --drain 10`} label="From a terminal" />

      <Dialog
        open={confirming !== null}
        onOpenChange={close}
        title={confirming === "off" ? `Turn off zero downtime for ${domain}?` : `Turn on zero downtime for ${domain}?`}
        description={
          confirming === "off"
            ? "Activations go back to restarting a single unit, which drops connections for a moment. Switching back happens without a cut."
            : `Every activation will start the new release next to the one serving, move traffic once it passes the health check, and stop the old one ${String(pending?.drain ?? data?.drain_seconds ?? 0)} seconds later.`
        }
        footer={
          <>
            <Button disabled={working} onClick={() => close(false)}>
              Cancel
            </Button>
            {confirming === "off" ? (
              <Button variant="danger" loading={working} onClick={() => change.mutate({ change: "off", drain: null })}>
                Turn off zero downtime
              </Button>
            ) : (
              <Button variant="primary" loading={working} onClick={() => change.mutate({ change: "on", drain: pending?.drain ?? null })}>
                Turn on zero downtime
              </Button>
            )}
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {confirming === "on" ? <TwoCopiesWarning /> : null}
          {working ? (
            <p role="status" className="text-13 text-fg-muted">
              {confirming === "off"
                ? "Going back to one unit. This takes a few seconds."
                : "Starting the second instance and switching nginx. This takes a few seconds."}
            </p>
          ) : dialogFailure !== null ? (
            <ErrorBlock live compact error={dialogFailure} title={failureTitle} hint={failureHint} />
          ) : null}
        </div>
      </Dialog>
    </section>
  );
}
