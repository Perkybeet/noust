import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import type { App } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { useReasonRequired } from "../../settings/compliance/useReasonRequired";
import { useSudoFirst } from "./formParts";
import { sandboxQuery, settingsKeys } from "./queries";
import type { Sandbox } from "./queries";
import { SETTINGS_ICONS } from "./objectIcons";

/** The one type whose builds do not go through the sandbox (`UNSUPPORTED_TYPES` in the backend). */
const COMPOSE = "docker-compose";

/** The account sandboxed builds run as (`BUILD_USER` in the backend). */
const BUILD_USER = "noust-build";

/**
 * Asks for a reason and runs what needs one: building as root, or letting a compose stack have
 * what is root on the server. Both are recorded with the operator's name and the reason, and
 * both are behind sudo mode, asked before this opens.
 */
function ReasonDialog({
  open,
  onOpenChange,
  title,
  description,
  action,
  failed,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  action: string;
  failed: string;
  onSubmit: (reason: string) => Promise<unknown>;
}) {
  const t = useT();
  const reasonRequired = useReasonRequired();
  const [reason, setReason] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const run = useMutation({
    mutationFn: onSubmit,
    onSuccess: () => {
      onOpenChange(false);
    },
  });
  const empty = reasonRequired && reason.trim() === "";
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (empty || run.isPending) return;
    run.mutate(reason.trim());
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && run.isPending) return;
        onOpenChange(next);
      }}
      title={title}
      description={description}
      footer={
        <>
          <Button disabled={run.isPending} onClick={() => onOpenChange(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form="builds-reason" variant="primary" loading={run.isPending}>
            {action}
          </Button>
        </>
      }
    >
      <form id="builds-reason" onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t("appSettings.builds.reasonLabel")}
          description={t("appSettings.builds.reasonDescription")}
          optional={!reasonRequired}
          error={submitted && empty ? t("appSettings.builds.reasonRequired") : undefined}
        >
          <Textarea rows={3} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
        </Field>
        {run.isError ? <ErrorBlock live compact error={run.error} title={failed} /> : null}
      </form>
    </Dialog>
  );
}

/** How the app builds now, when it builds in the sandbox: as whom, with what network. */
function OnFacts({ sandbox }: { sandbox: Sandbox }) {
  const t = useT();
  const items: KeyValueItem[] = [
    { label: t("appSettings.builds.runsAs"), value: BUILD_USER },
    {
      label: t("appSettings.builds.network"),
      value: sandbox.network === "strict" ? t("appSettings.builds.networkStrict") : t("appSettings.builds.networkFull"),
      mono: false,
      copy: false,
      ...(sandbox.pty ? { hint: t("appSettings.builds.ptyHint") } : {}),
    },
  ];
  if (sandbox.changed_at) {
    items.push({
      label: t("appSettings.builds.since"),
      value: sandbox.changed_by
        ? t.rich("appSettings.builds.sinceBy", { time: <RelativeTime value={sandbox.changed_at} />, who: <Mono>{sandbox.changed_by}</Mono> })
        : <RelativeTime value={sandbox.changed_at} />,
      mono: false,
      copy: false,
    });
  }
  return <KeyValueList items={items} />;
}

/** The last trial build: when, which commit, and the build's own words when it failed. */
function Trial({ sandbox }: { sandbox: Sandbox }) {
  const t = useT();
  const trial = sandbox.trial;
  if (trial === null || trial === undefined) {
    return sandbox.enabled ? null : <p className="text-13 text-fg-muted">{t("appSettings.builds.noTrial")}</p>;
  }
  const commit = trial.commit ? trial.commit.slice(0, 7) : null;
  if (trial.passed) {
    return (
      <p className="text-13 text-pretty text-fg-muted">
        {commit !== null
          ? t.rich("appSettings.builds.trialPassedCommit", { time: <RelativeTime value={trial.tested_at} />, commit: <Mono>{commit}</Mono> })
          : t.rich("appSettings.builds.trialPassed", { time: <RelativeTime value={trial.tested_at} /> })}
      </p>
    );
  }
  return (
    <ErrorBlock
      compact
      error={{ detail: trial.detail ?? t("appSettings.builds.trialFailedNoDetail") }}
      title={t("appSettings.builds.trialFailed")}
      hint={t("appSettings.builds.trialFailedHint")}
    />
  );
}

/**
 * Sandboxed builds for a type that has them: the state in the card's header, the last trial, and the
 * way on (a test build as a job, then turning it on once it passed) or off (building as root, with a
 * reason). The card's buttons are what moves it from one state to the next.
 */
function SandboxCard({ app, sandbox }: { app: App; sandbox: Sandbox }) {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const followed = useFollowedJob();
  const handledRef = useRef<string | null>(null);
  const [disabling, setDisabling] = useState(false);

  const test = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/sandbox/test", { params: { domain } }),
    onSuccess: (result) => {
      followed.follow(result.job as Job);
    },
  });
  const enable = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/sandbox/enable", { params: { domain }, body: { force: false } }),
    onSuccess: (result) => {
      queryClient.setQueryData(settingsKeys.sandbox(domain), result);
      followed.dismiss();
      announce(t("appSettings.builds.turnedOn", { domain }));
    },
  });

  const job = followed.job;
  const testing = test.isPending || (job !== null && !isJobFinished(job));
  const jobFailed = job !== null && isJobFinished(job) && job.status !== "completed";
  const passedNow = job !== null && job.status === "completed" && (job.result as { passed?: unknown } | null)?.passed === true;

  // The trial's outcome is recorded with the app's regime: read it again when the job ends.
  useEffect(() => {
    if (job === null || !isJobFinished(job) || handledRef.current === job.id) return;
    handledRef.current = job.id;
    void queryClient.invalidateQueries({ queryKey: settingsKeys.sandbox(domain) });
  }, [job, domain, queryClient]);

  const trialPassed = sandbox.trial?.passed === true;
  const decidedBy = sandbox.changed_by ?? t("appSettings.builds.anOperator");
  return (
    <Card
      icon={SETTINGS_ICONS.sandbox}
      title={t("appSettings.builds.cardTitle")}
      status={
        sandbox.enabled ? (
          <StatusPill state="running" size="sm" label={t("appSettings.builds.on")} />
        ) : sandbox.mode === "off" ? (
          <StatusPill state="stopped" size="sm" label={t("appSettings.builds.off")} />
        ) : (
          <StatusPill state="warning" size="sm" label={t("appSettings.builds.notYet")} />
        )
      }
      description={sandbox.enabled ? t("appSettings.builds.onBody") : sandbox.mode === "off" ? t("appSettings.builds.offTitle", { who: decidedBy }) : t("appSettings.builds.legacyTitle")}
      actions={
        sandbox.enabled ? (
          <>
            <Button size="sm" variant="ghost" loading={testing} onClick={() => test.mutate()}>
              {t("appSettings.builds.testAgain")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                sudoFirst(() => setDisabling(true), t("appSettings.builds.notChanged", { domain }));
              }}
            >
              {t("appSettings.builds.buildAsRoot")}
            </Button>
          </>
        ) : (
          <>
            <Button icon={<SETTINGS_ICONS.sandbox aria-hidden="true" />} size="sm" loading={testing} onClick={() => test.mutate()}>
              {trialPassed ? t("appSettings.builds.testAgain") : t("appSettings.builds.test")}
            </Button>
            {trialPassed ? (
              <Button icon={<SETTINGS_ICONS.turnOn aria-hidden="true" />} size="sm" loading={enable.isPending} onClick={() => enable.mutate()}>
                {t("appSettings.builds.turnOn")}
              </Button>
            ) : null}
          </>
        )
      }
    >
      <div className="flex flex-col gap-4">
        {sandbox.enabled ? (
          <OnFacts sandbox={sandbox} />
        ) : sandbox.mode === "off" ? (
          <div className="flex flex-col gap-1 text-13 text-pretty text-fg-muted">
            <span className="break-words">{sandbox.reason}</span>
            {sandbox.changed_at ? <span>{t.rich("appSettings.builds.offSince", { time: <RelativeTime value={sandbox.changed_at} /> })}</span> : null}
          </div>
        ) : (
          <p className="text-13 text-pretty text-fg-muted">{t("appSettings.builds.legacyBody")}</p>
        )}

        <Trial sandbox={sandbox} />

        {testing ? (
          <p role="status" className="text-13 text-fg-muted">
            {t("appSettings.builds.testing")}
          </p>
        ) : test.isError ? (
          <ErrorBlock live compact error={test.error} title={t("appSettings.builds.testNotStarted")} />
        ) : jobFailed ? (
          <ErrorBlock live compact error={{ detail: job.error ?? t("appSettings.jobFailedSilently") }} title={t("appSettings.builds.testNotFinished")} />
        ) : passedNow && !sandbox.enabled ? (
          <InlineMessage tone="success" live>
            {t("appSettings.builds.passedNow")}
          </InlineMessage>
        ) : null}
        {enable.isError ? <ErrorBlock live compact error={enable.error} title={t("appSettings.builds.notTurnedOn")} /> : null}
        {!sandbox.enabled && !trialPassed ? <p className="text-12 text-fg-muted">{t("appSettings.builds.turnOnAfterTest")}</p> : null}
      </div>
      <ReasonDialog
        key={String(disabling)}
        open={disabling}
        onOpenChange={setDisabling}
        title={t("appSettings.builds.disableTitle", { domain })}
        description={t("appSettings.builds.disableDescription")}
        action={t("appSettings.builds.buildAsRootAction")}
        failed={t("appSettings.builds.notChanged", { domain })}
        onSubmit={async (reason) => {
          const result = await request("post", "/api/apps/{domain}/sandbox/disable", { params: { domain }, body: { reason } });
          queryClient.setQueryData(settingsKeys.sandbox(domain), result);
          announce(t("appSettings.builds.turnedOff", { domain }));
        }}
      />
    </Card>
  );
}

/**
 * A compose stack builds inside Docker, not in the sandbox; what is guarded instead is what
 * would be root on the server (privileged containers, the Docker socket), refused unless this
 * stack was allowed it, with a reason.
 */
function ComposeCard({ app, sandbox }: { app: App; sandbox: Sandbox }) {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const [allowing, setAllowing] = useState(false);
  const exception = sandbox.compose_exception ?? null;
  const revoke = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/sandbox/compose-exception", { params: { domain } }),
    onSuccess: (result) => {
      queryClient.setQueryData(settingsKeys.sandbox(domain), result);
    },
  });
  return (
    <Card title={t("appSettings.builds.composeTitle")} icon={SETTINGS_ICONS.container} description={t("appSettings.builds.composeDescription")}>
      <div className="flex flex-col gap-4">
        {exception !== null ? (
          <KeyValueList
            items={[
              { label: t("appSettings.builds.composeAllowed"), value: t("appSettings.builds.composeAllowedValue"), mono: false, copy: false },
              { label: t("appSettings.builds.composeWhy"), value: exception.reason, mono: false, copy: false },
              {
                label: t("appSettings.builds.since"),
                value: t.rich("appSettings.builds.sinceBy", { time: <RelativeTime value={exception.allowed_at} />, who: <Mono>{exception.allowed_by}</Mono> }),
                mono: false,
                copy: false,
              },
            ]}
          />
        ) : (
          <p className="text-13 text-pretty text-fg-muted">{t("appSettings.builds.composeRefused")}</p>
        )}
        {sandbox.warning ? (
          <Notice tone="warning" title={t("appSettings.builds.composeWarningTitle")}>
            <span className="break-words">{sandbox.warning}</span>
          </Notice>
        ) : null}
        {revoke.isError ? <ErrorBlock live compact error={revoke.error} title={t("appSettings.builds.notChanged", { domain })} /> : null}
        <div>
          {exception !== null ? (
            <Button size="sm" variant="ghost" loading={revoke.isPending} onClick={() => revoke.mutate()}>
              {t("appSettings.builds.composeRevoke")}
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                sudoFirst(() => setAllowing(true), t("appSettings.builds.notChanged", { domain }));
              }}
            >
              {t("appSettings.builds.composeAllow")}
            </Button>
          )}
        </div>
      </div>
      <ReasonDialog
        key={String(allowing)}
        open={allowing}
        onOpenChange={setAllowing}
        title={t("appSettings.builds.composeAllowTitle", { domain })}
        description={t("appSettings.builds.composeAllowDescription")}
        action={t("appSettings.builds.composeAllowAction")}
        failed={t("appSettings.builds.notChanged", { domain })}
        onSubmit={async (reason) => {
          const result = await request("put", "/api/apps/{domain}/sandbox/compose-exception", { params: { domain }, body: { reason } });
          queryClient.setQueryData(settingsKeys.sandbox(domain), result);
        }}
      />
    </Card>
  );
}

/**
 * The sandbox builds run in, as a card of Build: installing dependencies and building happen as an
 * account that cannot read this server's secrets or change the system. What it protects, the state, a
 * test build as a job, turning it on once one passed, and turning it off only with a reason. A compose
 * stack builds inside Docker, so what it shows is what the stack is allowed that is root.
 */
export function SandboxSettings({ app }: { app: App }) {
  const t = useT();
  const type = app.app_type ?? "";
  // Every type has a regime to read, a monorepo's included: the backend says how it builds.
  const sandbox = useQuery(sandboxQuery(app.domain));

  if (sandbox.isPending) {
    return (
      <LoadingRegion label={t("appSettings.builds.loading")}>
        <SkeletonCard lines={3} />
      </LoadingRegion>
    );
  }
  if (sandbox.isError) {
    return <ErrorBlock error={sandbox.error} title={t("appSettings.builds.readFailed")} onRetry={() => void sandbox.refetch()} retrying={sandbox.isRefetching} />;
  }
  return type === COMPOSE ? <ComposeCard app={app} sandbox={sandbox.data} /> : <SandboxCard app={app} sandbox={sandbox.data} />;
}
