import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { isApiError, request } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys, migrationPlanQuery, releasesQuery } from "../../../api/queries/apps";
import type { App, MigrationPlan } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Stepper } from "../../../components/page/Stepper";
import type { WizardStep } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { translate } from "../../../i18n/translate";
import { formatBytes, formatCount } from "../../../lib/format";
import { reportActionError } from "../../apps/useAppActions";
import { useConfirmItsYou } from "../useDeleteApp";
import type { FormPart } from "./formParts";
import { RETENTION_MAX, RETENTION_MIN, parseRetention } from "./healthCheck";
import { MigrationPlanView } from "./MigrationPlanView";

// ---------------------------------------------------------------------------------------------
// How many versions are kept: a part of the Deploys form, on an app with instant rollback.
// ---------------------------------------------------------------------------------------------

interface RetentionOutcome {
  keep_releases: number;
  pruned: string[];
}

/** What saving did: how many it keeps now and which versions it removed, by id. */
export function retentionOutcome(result: RetentionOutcome, locale: Locale = getLocale()): string {
  const kept = translate(locale, "appSettings.releases.versionCount", { count: result.keep_releases });
  if (result.pruned.length === 0) return translate(locale, "appSettings.retention.outcomeNoneRemoved", { kept });
  const removed = translate(locale, "appSettings.releases.versionCount", { count: result.pruned.length });
  return translate(locale, "appSettings.retention.outcomeRemoved", { kept, removed, list: result.pruned.join(", ") });
}

export interface RetentionPart {
  part: FormPart;
  draft: string;
  edit: (value: string) => void;
  error: string | undefined;
  unplaced: unknown;
  outcome: RetentionOutcome | null;
}

/**
 * How many versions an app with instant rollback keeps. Saving prunes at once rather than at
 * the next deploy, which deletes their folders, so it is behind sudo mode; the live version and
 * the one before it are always kept, whatever the number.
 */
export function useRetention(app: App): RetentionPart {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const current = String(app.keep_releases);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [submitted, setSubmitted] = useState(false);
  const [outcome, setOutcome] = useState<RetentionOutcome | null>(null);

  if (current !== baseline) {
    setBaseline(current);
    if (draft.trim() === baseline) setDraft(current);
  }

  const parsed = parseRetention(draft, t.locale);
  const save = useMutation({
    mutationFn: (keep: number) => request("patch", "/api/apps/{domain}/releases/retention", { params: { domain }, body: { keep } }),
    onSuccess: (result) => {
      // What was saved is what the form now says, whatever the app's detail says next.
      setDraft(String(result.keep_releases));
      setBaseline(String(result.keep_releases));
      setOutcome(result);
      setSubmitted(false);
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, keep_releases: result.keep_releases } : known));
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      void queryClient.invalidateQueries({ queryKey: appKeys.releases(domain) });
    },
  });

  // The store's refusal of a number is about this one field: it goes under it.
  const refusal =
    save.isError && isApiError(save.error) && save.error.status === 400
      ? save.error.hint
        ? `${save.error.detail}. ${save.error.hint}`
        : save.error.detail
      : undefined;
  const error = (submitted && parsed.error !== null ? parsed.error : undefined) ?? refusal;

  return {
    part: {
      changes: draft.trim() !== current ? 1 : 0,
      elevated: true,
      check: () => {
        setSubmitted(true);
        return parsed.keep !== null;
      },
      save: async () => {
        if (parsed.keep === null) return;
        await save.mutateAsync(parsed.keep);
      },
      discard: () => {
        setDraft(current);
        setSubmitted(false);
        setOutcome(null);
        save.reset();
      },
    },
    draft,
    edit: (value) => {
      setDraft(value);
      setOutcome(null);
      if (save.isError) save.reset();
    },
    error,
    unplaced: save.isError && refusal === undefined ? save.error : null,
    outcome,
  };
}

// ---------------------------------------------------------------------------------------------
// The card: on, with the live version and how many are kept; or off, and the way to turn it on.
// ---------------------------------------------------------------------------------------------

/** The version live now and how many are on disk to go back to. */
function Versions({ domain }: { domain: string }) {
  const t = useT();
  const releases = useQuery(releasesQuery(domain));
  if (releases.isPending) return <KeyValueListSkeleton rows={2} hints={[0]} />;
  if (releases.isError) {
    return <ErrorBlock compact error={releases.error} title={t("appSettings.releases.listFailed")} onRetry={() => void releases.refetch()} />;
  }
  const items = releases.data?.items ?? [];
  const live = items.find((release) => release.active);
  const onDisk = items.filter((release) => release.on_disk).length;
  const removed = items.length - onDisk;
  return (
    <KeyValueList
      empty={t("appSettings.releases.none")}
      items={[
        {
          label: t("appSettings.releases.live"),
          value: live ? live.id : null,
          hint: live
            ? live.commit
              ? t.rich("appSettings.releases.liveHintWithCommit", { commit: <Mono>{live.commit}</Mono>, time: <RelativeTime value={live.activated_at ?? live.created_at} /> })
              : t.rich("appSettings.releases.liveHintNoCommit", { time: <RelativeTime value={live.activated_at ?? live.created_at} /> })
            : undefined,
        },
        {
          label: t("appSettings.releases.onDisk"),
          value: t("appSettings.releases.versionCount", { count: onDisk }),
          mono: false,
          copy: false,
          hint: removed > 0 ? t("appSettings.releases.removedCount", { count: removed }) : t("appSettings.releases.onDiskHint"),
        },
      ]}
    />
  );
}

/** On: what is live, what can be gone back to, and how many versions are kept. */
function RollbackOn({ app, retention }: { app: App; retention: RetentionPart }) {
  const t = useT();
  return (
    <Card title={t("appSettings.releases.title")}>
      <div className="flex flex-col gap-4">
        <FeatureState state="on" title={t("appSettings.releases.on")}>
          {t("appSettings.releases.descriptionOn")}
        </FeatureState>
        <Versions domain={app.domain} />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <Field
            label={t("appSettings.retention.keepLabel")}
            description={t("appSettings.retention.keepDescription", { min: RETENTION_MIN, max: RETENTION_MAX })}
            error={retention.error}
            className="max-w-measure-help"
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              suffix={t("appSettings.retention.unitSuffix")}
              value={retention.draft}
              onValueChange={retention.edit}
              className="w-40"
            />
          </Field>
          <TextLink to="/apps/$domain/deployments" params={{ domain: app.domain }} size="ui" className="inline-flex items-center gap-1.5 self-start sm:self-auto">
            <RotateCcw aria-hidden="true" className="size-icon-sm" />
            {t("appSettings.releases.goBackLink")}
          </TextLink>
        </div>
        {retention.unplaced !== null ? <ErrorBlock live compact error={retention.unplaced} title={t("appSettings.retention.saveFailed")} /> : null}
        {retention.outcome !== null ? (
          <Notice tone="success" live>
            {retentionOutcome(retention.outcome, t.locale)}
          </Notice>
        ) : null}
      </div>
    </Card>
  );
}

/**
 * Off: the benefit first, then what changes on disk, that it restarts the app once, that it
 * undoes itself if anything fails, and the way in, whose first step is a dry run.
 */
function RollbackOff({ onTurnOn }: { onTurnOn: () => void }) {
  const t = useT();
  return (
    <Card title={t("appSettings.releases.title")}>
      <div className="flex flex-col gap-4">
        <FeatureState
          state="off"
          title={t("appSettings.releases.off")}
          action={
            <Button size="sm" onClick={onTurnOn}>
              {t("appSettings.releases.turnOn")}
            </Button>
          }
        >
          {t("appSettings.releases.benefit")}
        </FeatureState>
        <ul className="flex max-w-measure list-disc flex-col gap-1.5 pl-5 text-13 text-pretty text-fg-muted marker:text-fg-faint">
          <li>{t.rich("appSettings.releases.onDiskChange", { releases: <Mono>releases/</Mono>, shared: <Mono>shared/</Mono> })}</li>
          <li>{t("appSettings.releases.restartOnce")}</li>
          <li>{t("appSettings.releases.undoesItself")}</li>
        </ul>
        <p className="text-12 text-fg-muted">{t("appSettings.releases.dryRunFirst")}</p>
      </div>
    </Card>
  );
}

export function InstantRollbackCard({ app, retention }: { app: App; retention: RetentionPart }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {app.layout === "releases" ? <RollbackOn app={app} retention={retention} /> : <RollbackOff onTurnOn={() => setOpen(true)} />}
      {/* Outside the card: when the move ends the card becomes the "on" one, and the dialog
          stays to say how it went. */}
      <TurnOnRollback app={app} open={open} onOpenChange={setOpen} />
    </>
  );
}

// ---------------------------------------------------------------------------------------------
// Turning it on (T5 in a dialog): what changes (the dry run), confirm, done.
// ---------------------------------------------------------------------------------------------

/**
 * The summary a finished `migrate` job's `result` carries (see `migrate_app_job`): what the
 * move did, for the last step to say.
 */
interface MigrationSummary {
  release_id: string;
  persistent: string[];
  files_before: number;
  files_after: number;
  bytes_after: number;
}

type Step = "plan" | "confirm" | "done";

/** What the confirmation says the move will do, from the plan being confirmed. */
export function confirmation(plan: MigrationPlan, locale: Locale = getLocale()): string {
  const rewritten = translate(
    locale,
    plan.unit_rewrite && plan.site_rewrite
      ? "appSettings.releases.confirmRewriteBoth"
      : plan.unit_rewrite
        ? "appSettings.releases.confirmRewriteUnit"
        : plan.site_rewrite
          ? "appSettings.releases.confirmRewriteSite"
          : "appSettings.releases.confirmRewriteNone",
  );
  const kept = plan.persistent.length > 0 ? translate(locale, "appSettings.releases.confirmKept", { list: plan.persistent.join(", ") }) : "";
  return translate(locale, "appSettings.releases.confirmation", { kept, rewritten });
}

function PlanStep({ plan, onRetry }: { plan: UseQueryResult<MigrationPlan | null>; onRetry: () => void }) {
  const t = useT();
  if (plan.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3">
        <span className="text-13 text-fg-muted">{t("appSettings.releases.migrate.reading")}</span>
        <Skeleton className="h-10 w-full rounded-control" />
        <KeyValueListSkeleton rows={5} />
      </div>
    );
  }
  if (plan.isError) {
    return <ErrorBlock error={plan.error} title={t("appSettings.releases.migrate.planFailed")} onRetry={onRetry} retrying={plan.isRefetching} />;
  }
  if (plan.data === null) {
    // Already on releases (a 409): the app's own detail catches up in a moment.
    return <Notice>{t("appSettings.releases.migrate.alreadyOn")}</Notice>;
  }
  return (
    <div className="flex flex-col gap-3">
      <MigrationPlanView plan={plan.data} />
      <div>
        <Button size="sm" variant="ghost" loading={plan.isRefetching} onClick={onRetry}>
          {t("appSettings.releases.migrate.checkAgain")}
        </Button>
      </div>
    </div>
  );
}

/**
 * Moving an in-place app onto releases. The first step is a dry run: the plan is read from the
 * disk and nothing changes until the operator has read it and confirmed. The move itself runs
 * as a job, followed here to its end; the backend undoes it whole if the app does not come up.
 */
function TurnOnRollback({ app, open, onOpenChange }: { app: App; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const followed = useFollowedJob();
  const notifiedRef = useRef<string | null>(null);
  const [step, setStep] = useState<Step>("plan");
  const [summary, setSummary] = useState<MigrationSummary | null>(null);
  // Once moved the app has no plan: the backend answers 409, so it is not asked again.
  const plan = useQuery({ ...migrationPlanQuery(domain), enabled: open && step !== "done", refetchOnWindowFocus: false });

  const migrate = useMutation({
    mutationFn: (current: MigrationPlan) =>
      request("post", "/api/apps/{domain}/migrate", {
        params: { domain },
        // What was reviewed is what is kept; an empty list lets the backend look again.
        body: { persist: current.persistent.length > 0 ? current.persistent : null },
      }),
    onSuccess: (result) => {
      followed.follow(result.job as Job);
    },
  });

  const job = followed.job;
  const moving = migrate.isPending || (job !== null && !isJobFinished(job));
  const jobFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  useEffect(() => {
    if (job === null || !isJobFinished(job) || job.status !== "completed" || notifiedRef.current === job.id) return;
    notifiedRef.current = job.id;
    setSummary(job.result as unknown as MigrationSummary);
    setStep("done");
    // Not the app's whole prefix: its plan would be asked for again and answer 409.
    void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
    void queryClient.invalidateQueries({ queryKey: appKeys.releases(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.rollbackPoints(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    announce(t("appSettings.releases.migrate.doneAnnounce", { domain }));
  }, [job, domain, queryClient, t]);

  const close = (): void => {
    if (moving) return;
    onOpenChange(false);
    // Opened again, it starts from the dry run, with nothing of this attempt left.
    setStep("plan");
    setSummary(null);
    migrate.reset();
    followed.dismiss();
  };

  const turnOn = (): void => {
    const current = plan.data;
    if (current === null || current === undefined) return;
    migrate.reset();
    followed.dismiss();
    confirmItsYou().then(
      () => {
        migrate.mutate(current);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.releases.migrate.startFailed"), error);
      },
    );
  };

  const steps: WizardStep[] = [
    { id: "plan", label: t("appSettings.releases.migrate.stepPlan") },
    { id: "confirm", label: t("appSettings.releases.migrate.stepConfirm") },
    { id: "done", label: t("appSettings.releases.migrate.stepDone") },
  ];
  const planReady = plan.data !== null && plan.data !== undefined;

  let body;
  let actions;
  if (step === "plan") {
    body = (
      <>
        <p className="text-13 text-pretty text-fg-muted">{t("appSettings.releases.migrate.planIntro")}</p>
        <PlanStep plan={plan} onRetry={() => void plan.refetch()} />
      </>
    );
    actions = (
      <WizardActions
        className="w-full"
        back={{ label: t("appSettings.cancel"), onClick: close }}
        next={{ label: t("appSettings.releases.migrate.continue"), onClick: () => setStep("confirm") }}
        missing={planReady ? undefined : t("appSettings.releases.migrate.planMissing")}
      />
    );
  } else if (step === "confirm") {
    const failure = migrate.isError ? migrate.error : jobFailed ? { detail: job.error ?? t("appSettings.jobFailedSilently") } : null;
    body = (
      <>
        <p className="text-14 text-pretty text-fg">{plan.data ? confirmation(plan.data, t.locale) : null}</p>
        <Notice tone="warning" title={t("appSettings.releases.migrate.restartTitle")}>
          {t("appSettings.releases.migrate.restartBody")}
        </Notice>
        {moving ? (
          <p role="status" className="text-13 text-fg-muted">
            {t("appSettings.releases.migrate.moving")}
          </p>
        ) : failure !== null ? (
          <ErrorBlock live compact error={failure} title={t("appSettings.releases.migrate.notCompleted")} hint={t("appSettings.releases.migrate.revertedHint")} />
        ) : null}
      </>
    );
    actions = (
      <WizardActions
        className="w-full"
        back={{ onClick: () => (moving ? undefined : setStep("plan")) }}
        next={{ label: t("appSettings.releases.migrate.turnOn"), loading: moving, onClick: turnOn }}
      />
    );
  } else {
    body =
      summary !== null ? (
        <Notice tone="success" title={t.rich("appSettings.releases.migrate.done", { release: <Mono>{summary.release_id}</Mono> })}>
          {t("appSettings.releases.migrate.doneFiles", {
            filesBefore: formatCount(summary.files_before),
            filesAfter: formatCount(summary.files_after),
            bytes: formatBytes(summary.bytes_after),
          })}
          {summary.persistent.length > 0 ? ` ${t("appSettings.releases.migrate.doneKept", { list: summary.persistent.join(", ") })}` : ""}
        </Notice>
      ) : null;
    actions = <WizardActions className="w-full" next={{ label: t("appSettings.releases.migrate.finish"), onClick: close }} />;
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="lg"
      title={t("appSettings.releases.migrate.title", { domain })}
      footer={actions}
    >
      <div className="flex flex-col gap-4">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {body}
      </div>
    </Dialog>
  );
}
