import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import { previewsKey, previewsQuery } from "../../../api/queries/previews";
import type { Preview, Previews, PreviewSettings } from "../../../api/queries/previews";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { appNameOf, previewParentOf } from "../../apps/data";
import { reportActionError } from "../../apps/useAppActions";
import { splitErrors } from "../../settings/formErrors";
import { SharedVariables } from "../../variables/SharedVariables";
import { useOrganization } from "../../teams/useOrganization";
import { useConfirmItsYou } from "../useDeleteApp";
import { CreatePreviewDialog } from "./CreatePreviewDialog";
import { useSaveBar } from "./formParts";
import { PreviewLogsDrawer } from "./PreviewLogsDrawer";
import { PreviewRow } from "./PreviewRow";
import type { PreviewAbilities } from "./PreviewRow";
import {
  DEFAULT_TEMPLATE,
  MAX_PREVIEWS_MAX,
  MAX_PREVIEWS_MIN,
  PREVIEW_FIELDS,
  TEMPLATE_PLACEHOLDERS,
  parsePreviewDraft,
  previewChanges,
  previewDraftOf,
  previewFieldOf,
  samePreviewDraft,
  templateExample,
} from "./previews";
import type { DataMode, ForkPolicy, InheritMode, PreviewErrors, PreviewField, TtlUnit } from "./previews";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { SETTINGS_ICONS } from "./objectIcons";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

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

/** What previews need before a pull request gets one: a wildcard record and the pull request events. */
function Needs({ domain, base }: { domain: string; base: string | null }) {
  const t = useT();
  const shownBase = base ?? t("appSettings.previews.baseDomainUnset");
  return (
    <ul aria-label={t("appSettings.previews.needsLabel")} className="grid gap-3 sm:grid-cols-2">
      <Need icon={<OBJECT_ICONS.domain />} title={t("appSettings.previews.needWildcardTitle")}>
        {t.rich("appSettings.previews.needWildcardBody", {
          record: <Mono>{`*.${shownBase}`}</Mono>,
          example: <Mono className="break-all">{`pr-12-${appNameOf(domain)}.${shownBase}`}</Mono>,
        })}
      </Need>
      <Need icon={<OBJECT_ICONS.webhook />} title={t("appSettings.previews.needPrTitle")}>
        {t("appSettings.previews.needPrBody")}
      </Need>
    </ul>
  );
}

/**
 * The settings previews run under: where they answer, how many may exist at once and how long
 * one lives without a push, and what a preview is given (which variables, which data). Off, the
 * form's own button turns them on with its values; on, its changes are saved from the save bar,
 * and turning them off is a separate, confirmed action, because it removes every preview there is.
 */
function PreviewSettingsForm({
  domain,
  settings,
  previews,
  hasDatabase,
  children,
}: {
  domain: string;
  settings: PreviewSettings | null;
  previews: readonly Preview[];
  /** Whether the app has a database of its own: sharing it with previews is a risk only then. */
  hasDatabase: boolean;
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

  const edit = (field: "base_domain" | "max_previews" | "ttl_hours" | "exclude_env" | "allow_env" | "domain_template") => (value: string) => {
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
        icon={SETTINGS_ICONS.preview}
        title={t("appSettings.previews.stateTitle")}
        status={<StatusPill state={enabled ? "running" : "stopped"} size="sm" label={enabled ? t("appSettings.previews.on") : t("appSettings.previews.off")} />}
        {...(enabled ? {} : { description: t("appSettings.previews.offBody") })}
        actions={
          enabled ? (
            <Button icon={<SETTINGS_ICONS.turnOff aria-hidden="true" />} size="sm" variant="ghost" onClick={turnOff}>
              {t("appSettings.previews.turnOff")}
            </Button>
          ) : (
            <Button icon={<SETTINGS_ICONS.turnOn aria-hidden="true" />} size="sm" type="submit" form="preview-settings" loading={save.isPending}>
              {t("appSettings.previews.turnOn")}
            </Button>
          )
        }
      >
        {enabled ? undefined : <Needs domain={domain} base={null} />}
      </Card>
      <form id="preview-settings" onSubmit={enabled ? (event) => { event.preventDefault(); bar.onSave(); } : turnOn} noValidate className="flex flex-col gap-4">
        <Card title={t("appSettings.previews.settingsTitle")} icon={SETTINGS_ICONS.limits}>
          <div className="flex flex-col gap-5">
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
            <ChoiceCards<ForkPolicy>
              legend={t("rollout.previews.forkLegend")}
              value={draft.fork_policy}
              onValueChange={(fork_policy) => {
                setDraft((previous) => ({ ...previous, fork_policy }));
                if (save.isError) save.reset();
              }}
              options={[
                { value: "refuse", label: t("rollout.previews.forkRefuseLabel"), description: t("rollout.previews.forkRefuseDescription") },
                { value: "approve", label: t("rollout.previews.forkApproveLabel"), description: t("rollout.previews.forkApproveDescription") },
              ]}
            />
            {draft.fork_policy === "approve" ? <InlineMessage>{t("rollout.previews.forkApproveRisk")}</InlineMessage> : null}
          </div>
        </Card>

        <Card title={t("rollout.previews.nameTitle")} icon={SETTINGS_ICONS.addresses}>
          <div className="flex flex-col gap-5">
            <Field
              label={t("rollout.previews.templateLabel")}
              description={t("rollout.previews.templateDescription", { tokens: TEMPLATE_PLACEHOLDERS.map((name) => `{{${name}}}`).join(", "), default: DEFAULT_TEMPLATE })}
              error={errorOf("domain_template")}
              optional
            >
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder={DEFAULT_TEMPLATE}
                value={draft.domain_template}
                onValueChange={edit("domain_template")}
                onBlur={blur("domain_template")}
              />
            </Field>
            <p className="text-12 text-fg-muted" data-testid="template-example">
              {t.rich("rollout.previews.templateExample", { example: <Mono className="break-all">{templateExample(draft.domain_template, domain, draft.base_domain)}</Mono> })}
            </p>
            <Checkbox
              label={t("rollout.previews.noindexLabel")}
              description={t("rollout.previews.noindexDescription")}
              checked={draft.noindex}
              onCheckedChange={(noindex) => {
                setDraft((previous) => ({ ...previous, noindex }));
                if (save.isError) save.reset();
              }}
            />
          </div>
        </Card>

        <Card title={t("previewHand.given.title")} icon={SETTINGS_ICONS.database}>
          <div className="flex flex-col gap-5">
            <ChoiceCards<InheritMode>
              legend={t("previewHand.env.legend")}
              value={draft.inherit_env}
              onValueChange={(inherit_env) => {
                setDraft((previous) => ({ ...previous, inherit_env }));
                if (save.isError) save.reset();
              }}
              options={[
                { value: "allowlist", label: t("previewHand.env.allowlistLabel"), description: t("previewHand.env.allowlistDescription") },
                { value: "all", label: t("previewHand.env.allLabel"), description: t("previewHand.env.allDescription") },
              ]}
            />
            {draft.inherit_env === "all" ? <InlineMessage>{t("previewHand.exposure.allRisk")}</InlineMessage> : null}
            {draft.inherit_env === "allowlist" ? (
              <Field label={t("previewHand.env.allowLabel")} description={t("previewHand.env.allowDescription")} error={errorOf("allow_env")} optional>
                <Input
                  mono
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  placeholder={t("previewHand.env.allowPlaceholder")}
                  value={draft.allow_env}
                  onValueChange={edit("allow_env")}
                  onBlur={blur("allow_env")}
                />
              </Field>
            ) : (
              <Field label={t("appSettings.previews.neverCopiedLabel")} description={t("appSettings.previews.neverCopiedDescription")} error={errorOf("exclude_env")} optional>
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
            )}
            <ChoiceCards<DataMode>
              legend={t("previewHand.data.legend")}
              value={draft.data_mode}
              onValueChange={(data_mode) => {
                setDraft((previous) => ({ ...previous, data_mode }));
                if (save.isError) save.reset();
              }}
              options={[
                { value: "empty", label: t("previewHand.data.emptyLabel"), description: t("previewHand.data.emptyDescription") },
                { value: "clone", label: t("rollout.previews.dataCloneLabel"), description: t("rollout.previews.dataCloneDescription") },
                { value: "shared", label: t("previewHand.data.sharedLabel"), description: t("previewHand.data.sharedDescription") },
              ]}
            />
            {draft.data_mode === "shared" && hasDatabase ? <InlineMessage>{t("previewHand.exposure.sharedRisk")}</InlineMessage> : null}
          </div>
        </Card>

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
      </form>
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

/**
 * The previews there are: each with its state and what can be done to it, and "New preview" to
 * make one by hand. Its logs open beside the list.
 */
function PreviewList({ domain, data, onGithub, can }: { domain: string; data: Previews; onGithub: boolean; can: PreviewAbilities }) {
  const t = useT();
  const max = data.settings?.max_previews ?? null;
  const [creating, setCreating] = useState(false);
  const [logs, setLogs] = useState<Preview | null>(null);
  return (
    <Subsection
      title={t("appSettings.previews.previewsHeading")}
      description={
        max !== null
          ? t("appSettings.previews.ofAtMost", { count: formatCount(data.total), max: formatCount(max) })
          : t("appSettings.previews.previewCount", { count: data.total })
      }
      actions={
        data.enabled && can.deploy ? (
          <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => setCreating(true)}>
            {t("previewHand.list.newPreview")}
          </Button>
        ) : undefined
      }
    >
      {data.previews.length === 0 ? (
        <EmptyState variant="inline" title={data.enabled ? t("appSettings.previews.noPreviewsAtAllOn") : t("appSettings.previews.noPreviewsAtAllOff")} />
      ) : (
        <Card padding="none">
          <ul aria-label={t("appSettings.previews.previewsHeading")} className="flex flex-col divide-y divide-border">
            {data.previews.map((preview) => (
              <PreviewRow key={preview.number} domain={domain} preview={preview} can={can} onLogs={setLogs} />
            ))}
          </ul>
        </Card>
      )}
      <CreatePreviewDialog domain={domain} open={creating} onOpenChange={setCreating} onGithub={onGithub} />
      <PreviewLogsDrawer domain={domain} preview={logs} onClose={() => setLogs(null)} />
    </Subsection>
  );
}

/**
 * Previews: a short-lived copy of the app for each pull request opened against its repository
 * (or made by hand from a branch or a tag), at a name of its own, removed when the pull request
 * closes or after a while without a push. What they need (a wildcard record, pull request
 * events) and what they are given (only the variables chosen, and a database of their own, unless
 * the settings say otherwise) is said before they can be turned on and while they are on.
 */
export function PreviewsSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.previews", domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.previews");
  const parent = previewParentOf(app);
  const previews = useQuery({ ...previewsQuery(domain), enabled: parent === null });
  const org = useOrganization();
  const team = app.team ?? null;
  const can: PreviewAbilities = { deploy: org.can("apps.deploy", team), operate: org.can("apps.operate", team), manage: org.can("apps.manage", team) };

  if (parent !== null) {
    return (
      <Section title={t("appSettings.previews.title")} {...headerIcon}>
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
  const onGithub = /github\.com[:/]/i.test(app.source ?? "");
  return (
    <Section
      title={t("appSettings.previews.title")}
      description={t("appSettings.previews.description")}
    >
      {previews.isPending ? (
        <LoadingRegion label={t("appSettings.previews.loading")}>
          <SkeletonCard lines={3} />
        </LoadingRegion>
      ) : previews.isError || data === undefined ? (
        <ErrorBlock error={previews.error} title={t("appSettings.previews.readFailed")} onRetry={() => void previews.refetch()} retrying={previews.isRefetching} />
      ) : (
        <>
          <PreviewSettingsForm domain={domain} settings={settings} previews={data.previews} hasDatabase={data.has_database}>
            {data.enabled || data.previews.length > 0 ? <PreviewList domain={domain} data={data} onGithub={onGithub} can={can} /> : null}
            {data.enabled ? <SharedVariables scope={{ kind: "preview", domain }} name={domain} canManage={can.manage} level={3} command={false} inlineEmpty /> : null}
            {hint}
          </PreviewSettingsForm>
        </>
      )}
      {data === undefined ? hint : null}
    </Section>
  );
}
