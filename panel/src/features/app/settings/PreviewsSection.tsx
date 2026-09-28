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

const TTL_UNITS = [
  { value: "hours", label: "Hours" },
  { value: "days", label: "Days" },
] as const;

const JOB_SILENT = "The job failed without saying why. Its log is on the Activity page.";

function previewCount(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "preview" : "previews"}`;
}

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
  const example = `pr-12-${appNameOf(domain)}.${base ?? "<base domain>"}`;
  return (
    <div className="flex flex-col gap-3">
      <ul aria-label="What previews need" className="flex flex-col gap-3">
        <Need icon={<Globe />} title="A wildcard DNS record">
          <p>
            <code translate="no" className="text-12 text-fg">{`*.${base ?? "<base domain>"}`}</code>
            {" pointing at this server. Each preview answers at its own name, such as "}
            <code translate="no" className="text-12 break-all text-fg">
              {example}
            </code>
            , with a certificate of its own.
          </p>
        </Need>
        <Need icon={<Webhook />} title="Pull request events">
          <p>
            The app's deploy webhook with pull request events turned on as well as pushes (Pull requests on GitHub and Gitea, Merge
            request events on GitLab), or this server's GitHub App installed on the repository. A pull request from a fork never gets
            a preview.
          </p>
        </Need>
      </ul>
      <div className="flex items-start gap-2.5 rounded-control border border-warn/40 bg-warn-soft px-3 py-2.5">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
        <div className="flex min-w-0 flex-col gap-1 text-13 text-pretty">
          <p className="flex items-center gap-1.5 font-medium text-fg">
            <KeyRound aria-hidden="true" className="size-3.5 shrink-0 text-fg-muted" />
            Previews get this app's production secrets
          </p>
          <p className="text-fg">
            A preview is built as root, like every deploy, from the pull request's branch. It runs with the app's environment variables,
            except those never copied to previews, and connects to its databases. Anyone who can push a branch to the repository and
            open a pull request runs code with them on this server.
          </p>
          <p className="text-fg">
            Pull requests from forks never get a preview. On GitHub, only pull requests by the repository's owners, members and
            collaborators do. Bot accounts, such as Dependabot and Renovate, are refused unless bots are allowed below.
          </p>
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

  const parsed = parsePreviewDraft(draft);
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
      toast.success(enabled ? `Saved the preview settings of ${domain}` : `Previews are on for ${domain}`);
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
        `Previews are off for ${domain}`,
        result.removing.length > 0 ? { description: `Removing ${previewCount(result.removing.length)}: ${result.removing.join(", ")}.` } : undefined,
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
        if (!(error instanceof ElevationCancelledError)) reportActionError(`The preview settings of ${domain} were not saved`, error);
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
        if (!(error instanceof ElevationCancelledError)) reportActionError(`Previews of ${domain} were not turned off`, error);
      },
    );
  };

  const live = previews.filter((preview) => preview.status !== "removing");

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <Field
        label="Base domain"
        description={
          draft.base_domain.trim() !== "" ? (
            <>
              {"A wildcard record "}
              <code translate="no" className="text-12">{`*.${draft.base_domain.trim().replace(/^\*\./, "")}`}</code>
              {" must point at this server."}
            </>
          ) : (
            "The domain a wildcard record points at this server, such as previews.example.com."
          )
        }
        error={errorOf("base_domain")}
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="previews.example.com"
          value={draft.base_domain}
          onValueChange={edit("base_domain")}
          onBlur={blur("base_domain")}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="At most"
          description={`${String(MAX_PREVIEWS_MIN)} to ${String(MAX_PREVIEWS_MAX)} at once. Each is a running copy of the app with its own certificate.`}
          error={errorOf("max_previews")}
        >
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            suffix="previews"
            value={draft.max_previews}
            onValueChange={edit("max_previews")}
            onBlur={blur("max_previews")}
          />
        </Field>
        <div className="flex min-w-0 items-start gap-2">
          <Field
            label="Removed after"
            description="Without a push to the pull request. Up to 90 days."
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
            label="Unit of the time a preview lives"
            options={TTL_UNITS}
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
        label="Never copied to previews"
        description="Variable names, separated by commas or spaces, such as live payment keys. Every other variable of the app is copied; a preview that has one of these loses it at its next push."
        error={errorOf("exclude_env")}
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="STRIPE_SECRET_KEY, SMTP_PASSWORD"
          value={draft.exclude_env}
          onValueChange={edit("exclude_env")}
          onBlur={blur("exclude_env")}
        />
      </Field>
      <Checkbox
        label="Allow pull requests from bots"
        description="Dependabot, Renovate and other bot accounts. Their pull requests run code nobody has reviewed yet, with this app's secrets."
        checked={draft.allow_bots}
        onCheckedChange={(allow_bots) => {
          setDraft((previous) => ({ ...previous, allow_bots }));
          if (save.isError) save.reset();
        }}
      />

      {save.isError && formError !== null ? <ErrorBlock live compact error={formError} title="The preview settings were not saved" /> : null}
      {removalFailed ? (
        <ErrorBlock
          live
          compact
          error={{ detail: job.error ?? JOB_SILENT }}
          title="Some previews were not removed"
          hint="Previews are off: no pull request gets a new one. Remove what is left from the list below."
        />
      ) : null}

      <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
        {settings !== null ? (
          <div className="flex min-w-0 flex-col gap-0.5 text-12 text-fg-muted">
            <p>{`Now: at most ${previewCount(settings.max_previews)} under ${settings.base_domain}, each removed after ${lifetime(settings.ttl_hours)} without a push.`}</p>
            <p>
              {settings.exclude_env.length > 0 ? (
                <>
                  {"Never copied: "}
                  <span translate="no" className="mono break-all text-fg">
                    {settings.exclude_env.join(", ")}
                  </span>
                  {". "}
                </>
              ) : (
                "Every variable is copied. "
              )}
              {settings.allow_bots ? "Bots get previews." : "Bots get none."}
            </p>
          </div>
        ) : (
          <p className="text-12 text-fg-muted">Pull requests get no preview.</p>
        )}
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {enabled ? (
            <Button variant="ghost" onClick={turnOff}>
              Turn off previews
            </Button>
          ) : null}
          <Button type="submit" variant="primary" disabled={!dirty} loading={save.isPending}>
            {enabled ? "Save" : "Turn on previews"}
          </Button>
        </div>
      </div>

      <Dialog
        open={confirmOff}
        onOpenChange={(next) => {
          if (!next && !disable.isPending) setConfirmOff(false);
        }}
        size="sm"
        title={`Turn off previews for ${domain}?`}
        description={
          live.length > 0
            ? `Pull requests get no new preview, and the ${previewCount(live.length)} there are now are removed: their apps, certificates and records.`
            : "Pull requests opened from now on get no preview. There is none to remove."
        }
        footer={
          <>
            <Button disabled={disable.isPending} onClick={() => setConfirmOff(false)}>
              Cancel
            </Button>
            <Button variant="danger" loading={disable.isPending} onClick={() => disable.mutate()}>
              Turn off previews
            </Button>
          </>
        }
      >
        {disable.isError ? (
          <ErrorBlock live compact error={disable.error} title="Previews were not turned off" />
        ) : live.length > 0 ? (
          <ul aria-label="Previews that are removed" className="flex flex-col gap-1">
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
  const date = parseTimestamp(value);
  // The clock every time label shares, stepping each minute: enough to flip the word on time.
  const now = useNow(() => 60_000);
  const past = date !== null && date.getTime() <= now;
  return (
    <span>
      {past ? "Expired " : "Expires "}
      <RelativeTime value={value} className="text-fg" />
    </span>
  );
}

/** One pull request's preview: where it answers, its state, when it goes, and removing it now. */
function PreviewRow({ domain, preview }: { domain: string; preview: Preview }) {
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
    if (job.status === "completed") toast.success(`Removed the preview of pull request #${number}`);
  }, [job, domain, number, queryClient]);

  const view = removing && preview.status !== "removing" ? previewStatus("removing") : previewStatus(preview.status);

  const start = (): void => {
    remove.reset();
    followed.dismiss();
    confirmItsYou().then(
      () => {
        setConfirming(true);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(`The preview of pull request #${number} was not removed`, error);
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
              aria-label={`Open preview of pull request #${number} (opens in a new tab)`}
              className="inline-flex items-center gap-1 rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
            >
              Open preview
              <ExternalLink aria-hidden="true" className="size-3.5 shrink-0" />
            </a>
          </div>
          <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-12 text-fg-muted">
            {preview.head_sha ? (
              <span>
                {"Commit "}
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
          <Button size="sm" variant="ghost" disabled={removing} aria-label={`Remove the preview of pull request #${number}`} onClick={start}>
            Remove
          </Button>
        </div>
      </div>

      {preview.status === "failed" ? (
        <ErrorBlock
          compact
          error={{ detail: preview.error ?? "The build failed without saying why. The preview's own page has the deployment's log." }}
          title={`The last build of #${number} failed`}
          hint="Push to the pull request to build it again. The preview's own page has the deployment's log."
        />
      ) : null}
      {removalFailed ? (
        <ErrorBlock live compact error={{ detail: job.error ?? JOB_SILENT }} title={`The preview of #${number} was not removed`} />
      ) : null}

      <Dialog
        open={confirming}
        onOpenChange={(next) => {
          if (!next && !remove.isPending) setConfirming(false);
        }}
        size="sm"
        title={`Remove the preview of pull request #${number}?`}
        description={`Its app, certificate and record are removed: ${preview.domain} stops answering. The pull request itself is not touched.`}
        footer={
          <>
            <Button disabled={remove.isPending} onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              Remove preview
            </Button>
          </>
        }
      >
        {remove.isError ? <ErrorBlock live compact error={remove.error} title="The preview was not removed" /> : undefined}
      </Dialog>
    </li>
  );
}

function PreviewList({ domain, data }: { domain: string; data: Previews }) {
  const headingId = useId();
  const max = data.settings?.max_previews ?? null;
  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 id={headingId} className="text-14 font-medium text-fg">
          Previews
        </h3>
        <span className="text-12 text-fg-muted">
          {max !== null ? `${formatCount(data.total)} of at most ${formatCount(max)}` : previewCount(data.total)}
        </span>
      </div>
      {data.previews.length === 0 ? (
        <p className={`${PANEL} px-4 py-4 text-13 text-fg-muted`}>
          {data.enabled
            ? "No pull request has a preview yet. One appears here when a pull request is opened against the repository."
            : "No previews."}
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
  const domain = app.domain;
  const parent = previewParentOf(app);
  const previews = useQuery({ ...previewsQuery(domain), enabled: parent === null });

  if (parent !== null) {
    return (
      <Section title="Pull request previews">
        <div className={`${PANEL} flex flex-col gap-1 px-4 py-4 text-13`}>
          <p className="text-fg">
            {"This app is a preview of "}
            <Link to="/apps/$domain/settings" params={{ domain: parent }} translate="no" className={LINK}>
              {parent}
            </Link>
            .
          </p>
          <p className="text-pretty text-fg-muted">Previews are set on the application itself; a preview cannot have previews of its own.</p>
        </div>
      </Section>
    );
  }

  const data = previews.data;
  const settings = data?.settings ?? null;

  return (
    <Section
      title="Pull request previews"
      description="Each pull request opened against the repository gets a copy of the app of its own, removed when the pull request closes or goes quiet."
    >
      <div className={`${PANEL} flex flex-col gap-5 px-4 py-4 sm:px-5`}>
        {previews.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-3">
            <span className="sr-only">Loading the preview settings</span>
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-24 w-full rounded-control" />
          </div>
        ) : previews.isError || data === undefined ? (
          <ErrorBlock compact error={previews.error} title="Could not read the previews" onRetry={() => void previews.refetch()} retrying={previews.isRefetching} />
        ) : (
          <>
            <div className="flex items-center gap-3">
              <GitPullRequest aria-hidden="true" className="size-4 shrink-0 text-fg-faint" />
              {data.enabled ? <StatusPill state="running" label="On" size="sm" /> : <StatusPill state="stopped" label="Off" size="sm" />}
              <p className="text-13 text-fg-muted">
                {data.enabled ? "Pull requests get a preview." : "Pull requests get no preview until previews are turned on."}
              </p>
            </div>
            <Needs domain={domain} base={settings?.base_domain ?? null} />
            <PreviewSettingsForm domain={domain} settings={settings} previews={data.previews} />
          </>
        )}
      </div>
      {data !== undefined && (data.enabled || data.previews.length > 0) ? <PreviewList domain={domain} data={data} /> : null}
      <CommandHint command={`wasm preview enable ${domain} --domain previews.example.com --max 3 --ttl 7d`} label="From a terminal" />
    </Section>
  );
}
