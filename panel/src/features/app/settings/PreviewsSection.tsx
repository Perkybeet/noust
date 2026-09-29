import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ExternalLink, GitPullRequest, Globe, KeyRound, TriangleAlert, Webhook } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { previewsKey, previewsQuery } from "../../../api/queries/previews";
import type { Preview, Previews, PreviewSettings } from "../../../api/queries/previews";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { useNow } from "../../../components/page/clock";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { formatCount, parseTimestamp } from "../../../lib/format";
import { appNameOf, previewParentOf } from "../../apps/data";
import { reportActionError } from "../../apps/useAppActions";
import { splitErrors } from "../../settings/formErrors";
import { useConfirmItsYou } from "../useDeleteApp";
import { LINK, PANEL } from "./panel";
import {
  MAX_PREVIEWS_MAX,
  MAX_PREVIEWS_MIN,
  PREVIEW_FIELDS,
  lifetime,
  parsePreviewDraft,
  previewDraftOf,
  previewFieldOf,
  previewStatus,
  samePreviewDraft,
} from "./previews";
import type { PreviewDraft, PreviewErrors, PreviewField, TtlUnit } from "./previews";

type Disabled = ResponseOf<"/api/apps/{domain}/previews/settings", "delete">;

/** One thing previews need, with what it is for. */
function Need({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5">
      <span aria-hidden="true" className="mt-0.5 flex shrink-0 text-fg-faint [&_svg]:size-4">
        {icon}
      </span>
      <div className="flex min-w-0 flex-col gap-0.5 text-13">
        <p className="font-medium text-fg">{title}</p>
        <div className="text-pretty text-fg-muted">{children}</div>
      </div>
    </li>
  );
}

/**
 * What previews need before a pull request gets one, and what a preview gets that the operator
 * must know about before turning them on: the app's own secrets and databases.
 */
function Needs({ domain, base }: { domain: string; base: string | null }) {
  const t = useT();
  const shownBase = base ?? t("appSettings.previews.baseDomainUnset");
  const example = `pr-12-${appNameOf(domain)}.${shownBase}`;
  return (
    <div className="flex flex-col gap-3">
      <ul aria-label={t("appSettings.previews.needsLabel")} className="flex flex-col gap-3">
        <Need icon={<Globe />} title={t("appSettings.previews.needWildcardTitle")}>
          <p>
            {t.rich("appSettings.previews.needWildcardBody", {
              record: <code translate="no" className="text-12 text-fg">{`*.${shownBase}`}</code>,
              example: (
                <code translate="no" className="text-12 break-all text-fg">
                  {example}
                </code>
              ),
            })}
          </p>
        </Need>
        <Need icon={<Webhook />} title={t("appSettings.previews.needPrTitle")}>
          <p>{t("appSettings.previews.needPrBody")}</p>
        </Need>
      </ul>
      <div className="flex items-start gap-2.5 rounded-control border border-warn/40 bg-warn-soft px-3 py-2.5">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
        <div className="flex min-w-0 flex-col gap-1 text-13 text-pretty">
          <p className="flex items-center gap-1.5 font-medium text-fg">
            <KeyRound aria-hidden="true" className="size-3.5 shrink-0 text-fg-muted" />
            {t("appSettings.previews.secretsWarningTitle")}
          </p>
          <p className="text-fg">{t("appSettings.previews.secretsWarningBody1")}</p>
          <p className="text-fg">{t("appSettings.previews.secretsWarningBody2")}</p>
        </div>
      </div>
    </div>
  );
}

/**
 * The settings previews run under: where they answer, how many may exist at once and how long
 * one lives without a push. Saving the first time is what turns previews on; turning them off
 * is a separate, confirmed action, because it removes every preview there is.
 */
function PreviewSettingsForm({ domain, settings, previews }: { domain: string; settings: PreviewSettings | null; previews: readonly Preview[] }) {
  const t = useT();
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const followed = useFollowedJob();
  const notifiedRef = useRef<string | null>(null);
  const enabled = settings !== null;

  const current = previewDraftOf(settings);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [touched, setTouched] = useState<Partial<Record<PreviewField, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);

  // The settings changed underneath an untouched form (saved here, or from a terminal): follow them.
  if (!samePreviewDraft(current, baseline)) {
    setBaseline(current);
    if (samePreviewDraft(draft, baseline)) setDraft(current);
  }

  const parsed = parsePreviewDraft(draft, t.locale);
  const dirty = !enabled || !samePreviewDraft(draft, current);

  const save = useMutation({
    mutationFn: () => {
      if (parsed.values === null) throw new Error("The form has errors");
      return request("put", "/api/apps/{domain}/previews/settings", { params: { domain }, body: parsed.values });
    },
    onSuccess: (result) => {
      setSubmitted(false);
      setTouched({});
      // What was saved, as the backend stored it: the names listed once, in its spelling.
      setDraft(previewDraftOf(result));
      queryClient.setQueryData<Previews>(previewsKey(domain), (known) => (known ? { ...known, enabled: true, settings: result } : known));
      void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
      // The toast is announced; saying it again would read it twice.
      toast.success(enabled ? t("appSettings.previews.savedSettingsToast", { domain }) : t("appSettings.previews.turnedOnToast", { domain }));
    },
  });

  const disable = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/previews/settings", { params: { domain } }),
    onSuccess: (result: Disabled) => {
      setConfirmOff(false);
      if (result.job_id) followed.follow(result.job_id);
      queryClient.setQueryData<Previews>(previewsKey(domain), (known) => (known ? { ...known, enabled: false, settings: null } : known));
      void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
      toast.success(
        t("appSettings.previews.turnedOffToast", { domain }),
        result.removing.length > 0
          ? {
              description: t("appSettings.previews.removingList", {
                count: t("appSettings.previews.previewCount", { count: result.removing.length }),
                list: result.removing.join(", "),
              }),
            }
          : undefined,
      );
    },
  });

  // The removal of the previews there were runs as a job; when it ends, the list is read again.
  const job = followed.job;
  useEffect(() => {
    if (job === null || !isJobFinished(job) || notifiedRef.current === job.id) return;
    notifiedRef.current = job.id;
    void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
  }, [job, domain, queryClient]);
  const removalFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  // A refusal lands beside the field it is about: named in `fields`, or by the backend's words.
  const split = splitErrors(save.error, PREVIEW_FIELDS);
  const serverFields: PreviewErrors = { ...split.fields };
  let formError: unknown = split.form;
  if (isApiError(formError) && formError.status === 400 && formError.fields === null) {
    const field = previewFieldOf(formError.detail);
    if (field !== null) {
      serverFields[field] = formError.hint ? `${formError.detail}. ${formError.hint}` : formError.detail;
      formError = null;
    }
  }
  const errorOf = (field: PreviewField): string | undefined =>
    (submitted || touched[field] ? parsed.errors[field] : undefined) ?? serverFields[field];

  const edit = (field: Exclude<keyof PreviewDraft, "unit" | "allow_bots">) => (value: string) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    if (save.isError) save.reset();
  };
  const blur = (field: PreviewField) => () => {
    setTouched((previous) => ({ ...previous, [field]: true }));
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (parsed.values === null || !dirty || save.isPending) return;
    // Sudo mode is asked for up front rather than on the refusal, so the save is one request.
    confirmItsYou().then(
      () => {
        save.mutate();
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.previews.notSavedFor", { domain }), error);
      },
    );
  };

  const turnOff = (): void => {
    disable.reset();
    confirmItsYou().then(
      () => {
        setConfirmOff(true);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.previews.turnOffNotStarted", { domain }), error);
      },
    );
  };

  const live = previews.filter((preview) => preview.status !== "removing");
  const ttlUnits: readonly { value: TtlUnit; label: string }[] = [
    { value: "hours", label: t("appSettings.previews.hoursOption") },
    { value: "days", label: t("appSettings.previews.daysOption") },
  ];

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <Field
        label={t("appSettings.previews.baseDomainLabel")}
        description={
          draft.base_domain.trim() !== "" ? (
            t.rich("appSettings.previews.baseDomainWildcardHint", {
              record: <code translate="no" className="text-12">{`*.${draft.base_domain.trim().replace(/^\*\./, "")}`}</code>,
            })
          ) : (
            t("appSettings.previews.baseDomainDescription")
          )
        }
        error={errorOf("base_domain")}
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={t("appSettings.previews.baseDomainPlaceholder")}
          value={draft.base_domain}
          onValueChange={edit("base_domain")}
          onBlur={blur("base_domain")}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label={t("appSettings.previews.atMostLabel")}
          description={t("appSettings.previews.atMostDescription", { min: MAX_PREVIEWS_MIN, max: MAX_PREVIEWS_MAX })}
          error={errorOf("max_previews")}
        >
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            suffix={t("appSettings.previews.previewsSuffix")}
            value={draft.max_previews}
            onValueChange={edit("max_previews")}
            onBlur={blur("max_previews")}
          />
        </Field>
        <div className="flex min-w-0 items-start gap-2">
          <Field
            label={t("appSettings.previews.removedAfterLabel")}
            description={t("appSettings.previews.removedAfterDescription")}
            error={errorOf("ttl_hours")}
            className="min-w-0 flex-1"
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              value={draft.ttl_hours}
              onValueChange={edit("ttl_hours")}
              onBlur={blur("ttl_hours")}
            />
          </Field>
          <SegmentedControl<TtlUnit>
            label={t("appSettings.previews.ttlUnitLabel")}
            options={ttlUnits}
            value={draft.unit}
            onValueChange={(unit) => {
              setDraft((previous) => ({ ...previous, unit }));
              if (save.isError) save.reset();
            }}
            className="mt-6 h-8"
          />
        </div>
      </div>

      <Field
        label={t("appSettings.previews.neverCopiedLabel")}
        description={t("appSettings.previews.neverCopiedDescription")}
        error={errorOf("exclude_env")}
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={t("appSettings.previews.neverCopiedPlaceholder")}
          value={draft.exclude_env}
          onValueChange={edit("exclude_env")}
          onBlur={blur("exclude_env")}
        />
      </Field>
      <Checkbox
        label={t("appSettings.previews.allowBotsLabel")}
        description={t("appSettings.previews.allowBotsDescription")}
        checked={draft.allow_bots}
        onCheckedChange={(allow_bots) => {
          setDraft((previous) => ({ ...previous, allow_bots }));
          if (save.isError) save.reset();
        }}
      />

      {save.isError && formError !== null ? <ErrorBlock live compact error={formError} title={t("appSettings.previews.saveFailed")} /> : null}
      {removalFailed ? (
        <ErrorBlock
          live
          compact
          error={{ detail: job.error ?? t("appSettings.jobFailedSilently") }}
          title={t("appSettings.previews.someNotRemoved")}
          hint={t("appSettings.previews.someNotRemovedHint")}
        />
      ) : null}

      <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
        {settings !== null ? (
          <div className="flex min-w-0 flex-col gap-0.5 text-12 text-fg-muted">
            <p>
              {t("appSettings.previews.nowSummary", {
                count: t("appSettings.previews.previewCount", { count: settings.max_previews }),
                domain: settings.base_domain,
                lifetime: lifetime(settings.ttl_hours, t.locale),
              })}
            </p>
            <p>
              {settings.exclude_env.length > 0 ? (
                <>
                  {t.rich("appSettings.previews.neverCopiedNow", {
                    list: (
                      <span translate="no" className="mono break-all text-fg">
                        {settings.exclude_env.join(", ")}
                      </span>
                    ),
                  })}
                  {" "}
                </>
              ) : (
                <>
                  {t("appSettings.previews.everyVariableCopied")}
                  {" "}
                </>
              )}
              {settings.allow_bots ? t("appSettings.previews.botsGetPreviews") : t("appSettings.previews.botsGetNone")}
            </p>
          </div>
        ) : (
          <p className="text-12 text-fg-muted">{t("appSettings.previews.noPreviewsAtAll")}</p>
        )}
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {enabled ? (
            <Button variant="ghost" onClick={turnOff}>
              {t("appSettings.previews.turnOff")}
            </Button>
          ) : null}
          <Button type="submit" variant="primary" disabled={!dirty} loading={save.isPending}>
            {enabled ? t("appSettings.save") : t("appSettings.previews.turnOn")}
          </Button>
        </div>
      </div>

      <Dialog
        open={confirmOff}
        onOpenChange={(next) => {
          if (!next && !disable.isPending) setConfirmOff(false);
        }}
        size="sm"
        title={t("appSettings.previews.turnOffConfirmTitle", { domain })}
        description={
          live.length > 0
            ? t("appSettings.previews.turnOffConfirmWithLive", { count: t("appSettings.previews.previewCount", { count: live.length }) })
            : t("appSettings.previews.turnOffConfirmNoLive")
        }
        footer={
          <>
            <Button disabled={disable.isPending} onClick={() => setConfirmOff(false)}>
              {t("appSettings.cancel")}
            </Button>
            <Button variant="danger" loading={disable.isPending} onClick={() => disable.mutate()}>
              {t("appSettings.previews.turnOff")}
            </Button>
          </>
        }
      >
        {disable.isError ? (
          <ErrorBlock live compact error={disable.error} title={t("appSettings.previews.notTurnedOff")} />
        ) : live.length > 0 ? (
          <ul aria-label={t("appSettings.previews.previewsRemovedList")} className="flex flex-col gap-1">
            {live.map((preview) => (
              <li key={preview.number} translate="no" className="mono text-12 break-all text-fg">
                {preview.domain}
              </li>
            ))}
          </ul>
        ) : undefined}
      </Dialog>
    </form>
  );
}

/** When a preview goes: "Expires in 5d", or "Expired 2h ago" while its removal is pending. */
function Expiry({ value }: { value: string }) {
  const t = useT();
  const date = parseTimestamp(value);
  // The clock every time label shares, stepping each minute: enough to flip the word on time.
  const now = useNow(() => 60_000);
  const past = date !== null && date.getTime() <= now;
  return (
    <span>
      {past ? t("appSettings.previews.expired") : t("appSettings.previews.expires")}
      <RelativeTime value={value} className="text-fg" />
    </span>
  );
}

/** One pull request's preview: where it answers, its state, when it goes, and removing it now. */
function PreviewRow({ domain, preview }: { domain: string; preview: Preview }) {
  const t = useT();
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const followed = useFollowedJob();
  const notifiedRef = useRef<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const number = String(preview.number);

  const remove = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/previews/{number}", { params: { domain, number: preview.number } }),
    onSuccess: (result) => {
      setConfirming(false);
      followed.follow(result.job as Job);
      void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
    },
  });

  const job = followed.job;
  const removing = remove.isPending || (job !== null && !isJobFinished(job)) || preview.status === "removing";
  const removalFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  useEffect(() => {
    if (job === null || !isJobFinished(job) || notifiedRef.current === job.id) return;
    notifiedRef.current = job.id;
    void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    if (job.status === "completed") toast.success(t("appSettings.previews.removedOfPr", { number }));
  }, [job, domain, number, queryClient, t]);

  const view = removing && preview.status !== "removing" ? previewStatus("removing", t.locale) : previewStatus(preview.status, t.locale);

  const start = (): void => {
    remove.reset();
    followed.dismiss();
    confirmItsYou().then(
      () => {
        setConfirming(true);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.previews.notRemovedFor", { number }), error);
      },
    );
  };

  return (
    <li className="flex min-w-0 flex-col gap-3 px-4 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusPill state={view.state} label={view.label} size="sm" />
            <span className="flex items-center gap-1.5 text-14 font-medium text-fg">
              <GitPullRequest aria-hidden="true" className="size-4 shrink-0 text-fg-faint" />
              {`#${number}`}
            </span>
            <span translate="no" className="mono min-w-0 text-12 break-all text-fg-muted">
              {preview.branch}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <Link to="/apps/$domain" params={{ domain: preview.domain }} translate="no" className={`${LINK} break-all`}>
              {preview.domain}
            </Link>
            <a
              href={preview.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={t("appSettings.previews.openPreviewAria", { number })}
              className="inline-flex items-center gap-1 rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
            >
              {t("appSettings.previews.openPreview")}
              <ExternalLink aria-hidden="true" className="size-3.5 shrink-0" />
            </a>
          </div>
          <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-12 text-fg-muted">
            {preview.head_sha ? (
              <span>
                {t("appSettings.previews.commitLabel")}
                <span translate="no" className="mono text-fg">
                  {preview.head_sha.slice(0, 7)}
                </span>
              </span>
            ) : null}
            {preview.repository ? (
              <span translate="no">{`${preview.provider} ${preview.repository}`}</span>
            ) : (
              <span translate="no">{preview.provider}</span>
            )}
            <Expiry value={preview.expires_at} />
          </p>
        </div>
        <div className="shrink-0">
          <Button size="sm" variant="ghost" disabled={removing} aria-label={t("appSettings.previews.removeAria", { number })} onClick={start}>
            {t("appSettings.previews.remove")}
          </Button>
        </div>
      </div>

      {preview.status === "failed" ? (
        <ErrorBlock
          compact
          error={{ detail: preview.error ?? t("appSettings.previews.buildFailedDefault") }}
          title={t("appSettings.previews.lastBuildFailed", { number })}
          hint={t("appSettings.previews.pushAgainHint")}
        />
      ) : null}
      {removalFailed ? (
        <ErrorBlock live compact error={{ detail: job.error ?? t("appSettings.jobFailedSilently") }} title={t("appSettings.previews.previewNotRemoved", { number })} />
      ) : null}

      <Dialog
        open={confirming}
        onOpenChange={(next) => {
          if (!next && !remove.isPending) setConfirming(false);
        }}
        size="sm"
        title={t("appSettings.previews.removeConfirmTitle", { number })}
        description={t("appSettings.previews.removeConfirmDescription", { domain: preview.domain })}
        footer={
          <>
            <Button disabled={remove.isPending} onClick={() => setConfirming(false)}>
              {t("appSettings.cancel")}
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              {t("appSettings.previews.removePreview")}
            </Button>
          </>
        }
      >
        {remove.isError ? <ErrorBlock live compact error={remove.error} title={t("appSettings.previews.previewNotRemovedGeneric")} /> : undefined}
      </Dialog>
    </li>
  );
}

function PreviewList({ domain, data }: { domain: string; data: Previews }) {
  const t = useT();
  const headingId = useId();
  const max = data.settings?.max_previews ?? null;
  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 id={headingId} className="text-14 font-medium text-fg">
          {t("appSettings.previews.previewsHeading")}
        </h3>
        <span className="text-12 text-fg-muted">
          {max !== null
            ? t("appSettings.previews.ofAtMost", { count: formatCount(data.total), max: formatCount(max) })
            : t("appSettings.previews.previewCount", { count: data.total })}
        </span>
      </div>
      {data.previews.length === 0 ? (
        <p className={`${PANEL} px-4 py-4 text-13 text-fg-muted`}>
          {data.enabled ? t("appSettings.previews.noPreviewsAtAllOn") : t("appSettings.previews.noPreviewsAtAllOff")}
        </p>
      ) : (
        <ul aria-labelledby={headingId} className={`${PANEL} flex flex-col divide-y divide-border`}>
          {data.previews.map((preview) => (
            <PreviewRow key={preview.number} domain={domain} preview={preview} />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Pull request previews: a short-lived copy of the app for each pull request opened against its
 * repository, at a name of its own, removed when the pull request closes or after a while
 * without a push. What they need (a wildcard record, pull request events) and what they get
 * (the app's secrets and databases) is said before they can be turned on.
 */
export function PreviewsSection({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const parent = previewParentOf(app);
  const previews = useQuery({ ...previewsQuery(domain), enabled: parent === null });

  if (parent !== null) {
    return (
      <Section title={t("appSettings.previews.title")}>
        <div className={`${PANEL} flex flex-col gap-1 px-4 py-4 text-13`}>
          <p className="text-fg">
            {t.rich("appSettings.previews.previewOfParent", {
              parent: (
                <Link to="/apps/$domain/settings" params={{ domain: parent }} translate="no" className={LINK}>
                  {parent}
                </Link>
              ),
            })}
          </p>
          <p className="text-pretty text-fg-muted">{t("appSettings.previews.noOwnPreviews")}</p>
        </div>
      </Section>
    );
  }

  const data = previews.data;
  const settings = data?.settings ?? null;

  return (
    <Section title={t("appSettings.previews.title")} description={t("appSettings.previews.description")}>
      <div className={`${PANEL} flex flex-col gap-5 px-4 py-4 sm:px-5`}>
        {previews.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-3">
            <span className="sr-only">{t("appSettings.previews.loading")}</span>
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-24 w-full rounded-control" />
          </div>
        ) : previews.isError || data === undefined ? (
          <ErrorBlock
            compact
            error={previews.error}
            title={t("appSettings.previews.readFailed")}
            onRetry={() => void previews.refetch()}
            retrying={previews.isRefetching}
          />
        ) : (
          <>
            <div className="flex items-center gap-3">
              <GitPullRequest aria-hidden="true" className="size-4 shrink-0 text-fg-faint" />
              {data.enabled ? (
                <StatusPill state="running" label={t("appSettings.previews.on")} size="sm" />
              ) : (
                <StatusPill state="stopped" label={t("appSettings.previews.off")} size="sm" />
              )}
              <p className="text-13 text-fg-muted">{data.enabled ? t("appSettings.previews.getsPreview") : t("appSettings.previews.getsNoPreview")}</p>
            </div>
            <Needs domain={domain} base={settings?.base_domain ?? null} />
            <PreviewSettingsForm domain={domain} settings={settings} previews={data.previews} />
          </>
        )}
      </div>
      {data !== undefined && (data.enabled || data.previews.length > 0) ? <PreviewList domain={domain} data={data} /> : null}
      <CommandHint command={`noust preview enable ${domain} --domain previews.example.com --max 3 --ttl 7d`} label={t("appSettings.fromTerminal")} />
    </Section>
  );
}
