import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitPullRequest, Globe, Webhook } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { previewsKey, previewsQuery } from "../../../api/queries/previews";
import type { Preview, Previews, PreviewSettings } from "../../../api/queries/previews";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { useNow } from "../../../components/page/clock";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { formatCount, parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { appNameOf, previewParentOf } from "../../apps/data";
import { reportActionError } from "../../apps/useAppActions";
import { splitErrors } from "../../settings/formErrors";
import { useConfirmItsYou } from "../useDeleteApp";
import { useSaveBar } from "./formParts";
import {
  MAX_PREVIEWS_MAX,
  MAX_PREVIEWS_MIN,
  PREVIEW_FIELDS,
  lifetime,
  parsePreviewDraft,
  previewChanges,
  previewDraftOf,
  previewFieldOf,
  previewStatus,
  samePreviewDraft,
} from "./previews";
import type { PreviewDraft, PreviewErrors, PreviewField, TtlUnit } from "./previews";
import { SettingState } from "./SettingState";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

type Disabled = ResponseOf<"/api/apps/{domain}/previews/settings", "delete">;

/** One thing previews need, with what it is for. */
function Need({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5">
      <span aria-hidden="true" className="mt-0.5 flex shrink-0 text-fg-muted [&_svg]:size-icon-md">
        {icon}
      </span>
      <div className="flex min-w-0 flex-col gap-0.5 text-13">
        <p className="font-medium text-fg">{title}</p>
        <p className="text-pretty text-fg-muted">{children}</p>
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
  return (
    <div className="flex flex-col gap-3">
      <ul aria-label={t("appSettings.previews.needsLabel")} className="grid gap-3 sm:grid-cols-2">
        <Need icon={<Globe />} title={t("appSettings.previews.needWildcardTitle")}>
          {t.rich("appSettings.previews.needWildcardBody", {
            record: <Mono>{`*.${shownBase}`}</Mono>,
            example: <Mono className="break-all">{`pr-12-${appNameOf(domain)}.${shownBase}`}</Mono>,
          })}
        </Need>
        <Need icon={<Webhook />} title={t("appSettings.previews.needPrTitle")}>
          {t("appSettings.previews.needPrBody")}
        </Need>
      </ul>
      <Notice tone="warning" title={t("appSettings.previews.secretsWarningTitle")}>
        {t("appSettings.previews.secretsWarningBody")}
      </Notice>
    </div>
  );
}

/**
 * The settings previews run under: where they answer, how many may exist at once and how long
 * one lives without a push. Off, the form's own button turns them on with its values; on, its
 * changes are saved from the save bar, and turning them off is a separate, confirmed action,
 * because it removes every preview there is.
 */
function PreviewSettingsForm({
  domain,
  settings,
  previews,
  children,
}: {
  domain: string;
  settings: PreviewSettings | null;
  previews: readonly Preview[];
  /** What follows the form in the subsection (the list, the command), before the save bar. */
  children: ReactNode;
}) {
  const t = useT();
  const { node } = useNode();
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
      setBaseline(previewDraftOf(result));
      queryClient.setQueryData<Previews>(previewsKey(domain), (known) => (known ? { ...known, enabled: true, settings: result } : known));
      void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
      if (!enabled) announce(t("appSettings.previews.turnedOnToast", { domain }));
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
  const errorOf = (field: PreviewField): string | undefined => (submitted || touched[field] ? parsed.errors[field] : undefined) ?? serverFields[field];

  const edit = (field: Exclude<keyof PreviewDraft, "unit" | "allow_bots">) => (value: string) => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    if (save.isError) save.reset();
  };
  const blur = (field: PreviewField) => () => {
    setTouched((previous) => ({ ...previous, [field]: true }));
  };

  const bar = useSaveBar(
    [
      enabled
        ? {
            changes: previewChanges(draft, current),
            elevated: true,
            check: () => {
              setSubmitted(true);
              return parsed.values !== null;
            },
            save: async () => {
              await save.mutateAsync();
            },
            discard: () => {
              setDraft(current);
              setTouched({});
              setSubmitted(false);
              save.reset();
            },
          }
        : null,
    ],
    t("appSettings.previews.notSavedFor", { domain }),
  );

  const turnOn = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (parsed.values === null || save.isPending) return;
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
    <>
      <Card
        title={t("appSettings.previews.settingsTitle")}
        actions={
          enabled ? (
            <Button size="sm" variant="ghost" onClick={turnOff}>
              {t("appSettings.previews.turnOff")}
            </Button>
          ) : undefined
        }
      >
        <form id="preview-settings" onSubmit={enabled ? (event) => { event.preventDefault(); bar.onSave(); } : turnOn} noValidate className="flex flex-col gap-5">
          <div className="grid gap-5 sm:grid-cols-2">
            <Field
              label={t("appSettings.previews.baseDomainLabel")}
              description={
                draft.base_domain.trim() !== ""
                  ? t.rich("appSettings.previews.baseDomainWildcardHint", { record: <Mono>{`*.${draft.base_domain.trim().replace(/^\*\./, "")}`}</Mono> })
                  : t("appSettings.previews.baseDomainDescription")
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
                <Input mono inputMode="numeric" autoComplete="off" value={draft.ttl_hours} onValueChange={edit("ttl_hours")} onBlur={blur("ttl_hours")} />
              </Field>
              <SegmentedControl<TtlUnit>
                label={t("appSettings.previews.ttlUnitLabel")}
                options={ttlUnits}
                value={draft.unit}
                onValueChange={(unit) => {
                  setDraft((previous) => ({ ...previous, unit }));
                  if (save.isError) save.reset();
                }}
                className="mt-6 h-control-md"
              />
            </div>
          </div>
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
          {enabled ? (
            <p className="text-12 text-pretty text-fg-muted">
              {t("appSettings.previews.nowSummary", {
                count: t("appSettings.previews.previewCount", { count: settings.max_previews }),
                domain: settings.base_domain,
                lifetime: lifetime(settings.ttl_hours, t.locale),
              })}
            </p>
          ) : (
            <div>
              <Button type="submit" loading={save.isPending}>
                {t("appSettings.previews.turnOn")}
              </Button>
            </div>
          )}
        </form>
      </Card>
      <ConfirmDialog
        open={confirmOff}
        onOpenChange={setConfirmOff}
        friction="simple"
        server={node}
        title={t("appSettings.previews.turnOffConfirmTitle", { domain })}
        description={
          live.length > 0
            ? t("appSettings.previews.turnOffConfirmWithLive", { count: t("appSettings.previews.previewCount", { count: live.length }), list: live.map((preview) => preview.domain).join(", ") })
            : t("appSettings.previews.turnOffConfirmNoLive")
        }
        actionLabel={t("appSettings.previews.turnOff")}
        onConfirm={async () => {
          const result: Disabled = await request("delete", "/api/apps/{domain}/previews/settings", { params: { domain } });
          if (result.job_id) followed.follow(result.job_id);
          queryClient.setQueryData<Previews>(previewsKey(domain), (known) => (known ? { ...known, enabled: false, settings: null } : known));
          void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
          announce(t("appSettings.previews.turnedOffToast", { domain }));
        }}
      />
      {children}
      {enabled ? <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} /> : null}
    </>
  );
}

/** When a preview goes: "Expires in 5d", or "Expired 2h ago" while its removal is pending. */
function Expiry({ value }: { value: string }) {
  const t = useT();
  const date = parseTimestamp(value);
  // The clock every time label shares, stepping each minute: enough to flip the word on time.
  const now = useNow(() => 60_000);
  const past = date !== null && date.getTime() <= now;
  return <span>{t.rich(past ? "appSettings.previews.expired" : "appSettings.previews.expires", { time: <RelativeTime value={value} className="text-fg" /> })}</span>;
}

/** One pull request's preview: where it answers, its state, when it goes, and removing it now. */
function PreviewRow({ domain, preview }: { domain: string; preview: Preview }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const followed = useFollowedJob();
  const notifiedRef = useRef<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const number = String(preview.number);

  const job = followed.job;
  const removing = (job !== null && !isJobFinished(job)) || preview.status === "removing";
  const removalFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  useEffect(() => {
    if (job === null || !isJobFinished(job) || notifiedRef.current === job.id) return;
    notifiedRef.current = job.id;
    void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    if (job.status === "completed") announce(t("appSettings.previews.removedOfPr", { number }));
  }, [job, domain, number, queryClient, t]);

  const view = removing && preview.status !== "removing" ? previewStatus("removing", t.locale) : previewStatus(preview.status, t.locale);

  const start = (): void => {
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
    <li className="flex min-w-0 flex-col gap-3 px-5 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusPill state={view.state} label={view.label} size="sm" />
            <span className="flex items-center gap-1.5 text-14 font-medium text-fg">
              <GitPullRequest aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
              {`#${number}`}
            </span>
            <Mono tone="muted" className="min-w-0 break-all">
              {preview.branch}
            </Mono>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <TextLink to="/apps/$domain" params={{ domain: preview.domain }} translate="no" size="ui" className="break-all">
              {preview.domain}
            </TextLink>
            <ExternalLink href={preview.url} label={t("appSettings.previews.openPreviewAria", { number })}>
              {t("appSettings.previews.openPreview")}
            </ExternalLink>
          </div>
          <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-12 text-fg-muted">
            {preview.head_sha ? <span>{t.rich("appSettings.previews.commit", { commit: <Mono>{preview.head_sha.slice(0, 7)}</Mono> })}</span> : null}
            <span translate="no">{preview.repository ? `${preview.provider} ${preview.repository}` : preview.provider}</span>
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
      {removalFailed ? <ErrorBlock live compact error={{ detail: job.error ?? t("appSettings.jobFailedSilently") }} title={t("appSettings.previews.previewNotRemoved", { number })} /> : null}

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        friction="simple"
        server={node}
        title={t("appSettings.previews.removeConfirmTitle", { number })}
        description={t("appSettings.previews.removeConfirmDescription", { domain: preview.domain })}
        actionLabel={t("appSettings.previews.removePreview")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/previews/{number}", { params: { domain, number: preview.number } });
          followed.follow(result.job as Job);
          void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
        }}
      />
    </li>
  );
}

function PreviewList({ domain, data }: { domain: string; data: Previews }) {
  const t = useT();
  const max = data.settings?.max_previews ?? null;
  return (
    <Subsection
      title={t("appSettings.previews.previewsHeading")}
      description={
        max !== null
          ? t("appSettings.previews.ofAtMost", { count: formatCount(data.total), max: formatCount(max) })
          : t("appSettings.previews.previewCount", { count: data.total })
      }
    >
      {data.previews.length === 0 ? (
        <EmptyState variant="inline" title={data.enabled ? t("appSettings.previews.noPreviewsAtAllOn") : t("appSettings.previews.noPreviewsAtAllOff")} />
      ) : (
        <Card padding="none">
          <ul aria-label={t("appSettings.previews.previewsHeading")} className="flex flex-col divide-y divide-border">
            {data.previews.map((preview) => (
              <PreviewRow key={preview.number} domain={domain} preview={preview} />
            ))}
          </ul>
        </Card>
      )}
    </Subsection>
  );
}

/**
 * Previews: a short-lived copy of the app for each pull request opened against its repository,
 * at a name of its own, removed when the pull request closes or after a while without a push.
 * What they need (a wildcard record, pull request events) and what they get (the app's secrets
 * and databases) is said before they can be turned on.
 */
export function PreviewsSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.previews", domain);
  const parent = previewParentOf(app);
  const previews = useQuery({ ...previewsQuery(domain), enabled: parent === null });

  if (parent !== null) {
    return (
      <Section title={t("appSettings.previews.title")}>
        <Card>
          <div className="flex flex-col gap-1 text-13">
            <p className="text-fg">
              {t.rich("appSettings.previews.previewOfParent", {
                parent: (
                  <TextLink to="/apps/$domain/settings/previews" params={{ domain: parent }} translate="no" size="ui">
                    {parent}
                  </TextLink>
                ),
              })}
            </p>
            <p className="text-pretty text-fg-muted">{t("appSettings.previews.noOwnPreviews")}</p>
          </div>
        </Card>
      </Section>
    );
  }

  const data = previews.data;
  const settings = data?.settings ?? null;
  const hint = <CommandHint command={`noust preview enable ${domain} --domain previews.example.com --max 3 --ttl 7d`} label={t("appSettings.fromTerminal")} />;
  return (
    <Section
      title={t("appSettings.previews.title")}
      description={t("appSettings.previews.description")}
      badge={data !== undefined ? <SettingState on={data.enabled} label={data.enabled ? t("appSettings.previews.on") : t("appSettings.previews.off")} /> : undefined}
    >
      {previews.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-4">
          <span className="sr-only">{t("appSettings.previews.loading")}</span>
          <Skeleton className="h-24 w-full rounded-card" />
          <Skeleton className="h-48 w-full rounded-card" />
        </div>
      ) : previews.isError || data === undefined ? (
        <ErrorBlock error={previews.error} title={t("appSettings.previews.readFailed")} onRetry={() => void previews.refetch()} retrying={previews.isRefetching} />
      ) : (
        <>
          {/* What previews need is the way in: once they are on, only what they get stays said. */}
          {data.enabled ? (
            <Notice tone="warning" title={t("appSettings.previews.secretsWarningTitle")}>
              {t("appSettings.previews.secretsWarningBody")}
            </Notice>
          ) : (
            <Needs domain={domain} base={settings?.base_domain ?? null} />
          )}
          <PreviewSettingsForm domain={domain} settings={settings} previews={data.previews}>
            {data.enabled || data.previews.length > 0 ? <PreviewList domain={domain} data={data} /> : null}
            {hint}
          </PreviewSettingsForm>
        </>
      )}
      {data === undefined ? hint : null}
    </Section>
  );
}
